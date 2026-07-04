#!/usr/bin/env bash
#
# benchmark.sh - Measure Ollama model generation speed (tokens/sec).
#
# Requires: curl, jq, and a running Ollama server.

set -euo pipefail

# ----- defaults -----
URL="${OLLAMA_HOST:-http://localhost:11434}"
RUNS=3
TOKENS=200
PROMPT="Write a detailed explanation of how the water cycle works, including evaporation, condensation, precipitation, and collection. Cover each stage thoroughly."
OUTPUT=""
USE_ALL=0
MODEL_FILE=""
MODELS=()

usage() {
	cat <<'EOF'
Usage: ./benchmark.sh [options] [model ...]

Measure Ollama generation speed (tokens/sec) for one or more models.

Options:
  -a, --all            Benchmark every installed model (from `ollama list`)
  -f, --file FILE      Read model names from FILE (one per line; # comments allowed)
  -p, --prompt TEXT    Prompt to send (default: a fixed medium prompt)
  -n, --runs N         Timed runs per model to average (default: 3)
  -t, --tokens N       Tokens to generate per run / num_predict (default: 200)
  -u, --url URL        Ollama host (default: $OLLAMA_HOST or http://localhost:11434)
  -o, --output FILE    Also write results as CSV
  -h, --help           Show this help

Examples:
  ./benchmark.sh qwen3:latest gemma3:latest
  ./benchmark.sh --all -o results.csv
  ./benchmark.sh -f models.txt -n 5 -t 1000
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

# ----- parse args -----
while [[ $# -gt 0 ]]; do
	case "$1" in
		-a|--all) USE_ALL=1; shift ;;
		-f|--file) MODEL_FILE="${2:-}"; [[ -n "$MODEL_FILE" ]] || die "--file requires an argument"; shift 2 ;;
		-p|--prompt) PROMPT="${2:-}"; shift 2 ;;
		-n|--runs) RUNS="${2:-}"; shift 2 ;;
		-t|--tokens) TOKENS="${2:-}"; shift 2 ;;
		-u|--url) URL="${2:-}"; shift 2 ;;
		-o|--output) OUTPUT="${2:-}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		-*) die "unknown option: $1 (see --help)" ;;
		*) MODELS+=("$1"); shift ;;
	esac
done

command -v curl >/dev/null 2>&1 || die "curl is required but not found"
command -v jq >/dev/null 2>&1 || die "jq is required but not found"

[[ "$RUNS" =~ ^[0-9]+$ && "$RUNS" -ge 1 ]] || die "--runs must be a positive integer"
[[ "$TOKENS" =~ ^[0-9]+$ && "$TOKENS" -ge 1 ]] || die "--tokens must be a positive integer"

URL="${URL%/}"

# ----- resolve model list -----
if [[ -n "$MODEL_FILE" ]]; then
	[[ -f "$MODEL_FILE" ]] || die "model file not found: $MODEL_FILE"
	while IFS= read -r line; do
		line="${line%%#*}"
		line="$(echo "$line" | xargs 2>/dev/null || true)"
		[[ -n "$line" ]] && MODELS+=("$line")
	done < "$MODEL_FILE"
fi

if [[ "$USE_ALL" -eq 1 ]]; then
	command -v ollama >/dev/null 2>&1 || die "--all requires the 'ollama' CLI"
	while IFS= read -r name; do
		[[ -n "$name" ]] && MODELS+=("$name")
	done < <(ollama list | awk 'NR>1 {print $1}')
fi

[[ "${#MODELS[@]}" -gt 0 ]] || { usage; echo; die "no models specified (pass model names, --file, or --all)"; }

# ----- preflight: server reachable? -----
if ! curl -sf "$URL/api/tags" >/dev/null 2>&1; then
	die "cannot reach Ollama server at $URL (is it running? try 'ollama serve')"
fi

# ----- request helper: prints "eval_count eval_duration" or nothing on failure -----
request_metrics() {
	local model="$1"
	local body
	body="$(jq -n \
		--arg model "$model" \
		--arg prompt "$PROMPT" \
		--argjson tokens "$TOKENS" \
		'{model: $model, prompt: $prompt, stream: false,
		  options: {seed: 42, temperature: 0, num_predict: $tokens, stop: []}}')"

	curl -sf "$URL/api/generate" -d "$body" 2>/dev/null \
		| jq -r 'select(.eval_count and .eval_duration) | "\(.eval_count) \(.eval_duration)"'
}

# ----- run benchmark -----
CURRENT_MODEL=""
trap '[[ -n "$CURRENT_MODEL" ]] && echo "  (interrupted while benchmarking: $CURRENT_MODEL)" >&2' ERR

SUMMARIES=()   # "avg\tmodel\truns\tmin\tmax\ttokens"

echo "Ollama speed benchmark @ $URL"
echo "runs=$RUNS  tokens=$TOKENS  models=${#MODELS[@]}"
echo

for model in "${MODELS[@]}"; do
	CURRENT_MODEL="$model"
	echo "== $model =="

	# warmup (load model into memory; result discarded)
	echo -n "  warmup... "
	if ! request_metrics "$model" >/dev/null; then
		echo "FAILED (skipping)"
		echo
		continue
	fi
	echo "done"

	sum=0; min=""; max=""; last_tokens=0; ok=0
	for ((i=1; i<=RUNS; i++)); do
		metrics="$(request_metrics "$model" || true)"
		if [[ -z "$metrics" ]]; then
			echo "  run $i/$RUNS: FAILED"
			continue
		fi
		read -r ec ed <<< "$metrics"
		tps="$(awk -v c="$ec" -v d="$ed" 'BEGIN { if (d > 0) printf "%.2f", c / (d/1e9); else print "0" }')"
		echo "  run $i/$RUNS: $tps tok/s ($ec tokens)"
		sum="$(awk -v s="$sum" -v t="$tps" 'BEGIN { printf "%.4f", s + t }')"
		last_tokens="$ec"; ok=$((ok+1))
		if [[ -z "$min" ]] || awk -v a="$tps" -v b="$min" 'BEGIN { exit !(a < b) }'; then min="$tps"; fi
		if [[ -z "$max" ]] || awk -v a="$tps" -v b="$max" 'BEGIN { exit !(a > b) }'; then max="$tps"; fi
	done

	if [[ "$ok" -eq 0 ]]; then
		echo "  no successful runs; skipping"
		echo
		continue
	fi

	avg="$(awk -v s="$sum" -v n="$ok" 'BEGIN { printf "%.2f", s / n }')"
	echo "  avg: $avg tok/s"
	echo
	SUMMARIES+=("$(printf '%s\t%s\t%s\t%s\t%s\t%s' "$avg" "$model" "$ok" "$min" "$max" "$last_tokens")")
done

CURRENT_MODEL=""
trap - ERR

[[ "${#SUMMARIES[@]}" -gt 0 ]] || die "no models produced results"

# ----- results table (sorted by avg tok/s desc) -----
echo "================ RESULTS (by tok/s) ================"
{
	printf 'MODEL\tRUNS\tAVG tok/s\tMIN\tMAX\tTOKENS\n'
	printf '%s\n' "${SUMMARIES[@]}" \
		| sort -t$'\t' -k1 -rn \
		| awk -F'\t' 'BEGIN{OFS="\t"} {print $2, $3, $1, $4, $5, $6}'
} | column -t -s$'\t'

# ----- optional CSV -----
if [[ -n "$OUTPUT" ]]; then
	{
		echo "model,runs,avg_tokens_per_sec,min_tokens_per_sec,max_tokens_per_sec,tokens"
		printf '%s\n' "${SUMMARIES[@]}" \
			| sort -t$'\t' -k1 -rn \
			| awk -F'\t' 'BEGIN{OFS=","} {print $2, $3, $1, $4, $5, $6}'
	} > "$OUTPUT"
	echo
	echo "CSV written to $OUTPUT"
fi
