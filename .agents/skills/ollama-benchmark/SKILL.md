---
name: ollama-benchmark
description: "Run and compare Ollama generation-speed benchmarks using this repository's benchmark.sh. Use when asked to benchmark installed Ollama models, save repeatable speed measurements, or compare previous benchmark runs."
---

# Ollama Benchmark

Use the existing `benchmark.sh` in the repository root, three directories above this skill's directory. Read the root `README.md` and the script's `--help` before running. Keep benchmark execution in that script.

## Prepare

- Resolve the server from the user's URL, then `OLLAMA_HOST`, then `http://localhost:11434`. Normalize it to a full HTTP(S) URL and pass it explicitly with `--url`.
- Check for Bash, `curl`, `jq`, `awk`, `sort`, and `column`. Fetch `/api/version` and `/api/tags` from that same server with short connection and total timeouts. Report missing prerequisites or an unreachable server before attempting a benchmark.
- Use the requested models or model file. When no models are specified, use the models returned by that server's `/api/tags`, once each. Pass their names as positional arguments: `--all` reads the local CLI's model list, which can differ from the selected server. The root `models.txt` contains examples, not an inventory.
- Check that selected models exist on the target server. Report missing models; pulling models or installing dependencies requires a request that includes that work.
- Honor requested settings. Otherwise use the script's defaults: three timed runs and a 200-token generation budget. For comparisons with a baseline, reuse its prompt, runs, and budget unless the user requests a change. Keep runs sequential to avoid contention.

## Run and preserve context

Create a fresh directory under `results/` with a UTC timestamp and unique suffix. Save:

- `context.md`: UTC start/end times, server URL and version, selected model names and digests from `/api/tags`, repository revision and working-tree state, script SHA-256, exact command and prompt, requested runs and token budget, and final exit status. Record server OS, CPU/GPU, and memory when known. For a remote server, mark unknown hardware as unknown; the client machine is not the benchmark machine.
- `run.log`: the complete script output, including failures and actual token counts for every successful timed run.
- `results.csv`: the script's summary export.
- `metadata.json`: the same machine-readable context, following [the saved-run format](../../../docs/run-metadata.md), so the dashboard can load the run. Save one model per directory when exporting to the dashboard.

The script uses one full warmup per model, `seed: 42`, `temperature: 0`, and `stop: []`. Record these settings and any known server defaults affecting context or device placement. Fixed options do not guarantee identical output across models or runtimes.

For example, from the repository root, substitute the selected server, models, and settings:

```bash
mkdir -p results
run_dir="$(mktemp -d "results/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
set -o pipefail
./benchmark.sh --url http://localhost:11434 --runs 3 --tokens 200 \
  --output "$run_dir/results.csv" qwen3:latest gemma3:latest \
  2>&1 | tee "$run_dir/run.log"
```

Write the initial context before starting and append the outcome afterward, including on failure. Preserve the benchmark exit status when capturing output through `tee`. Inspect both the log and CSV: a zero exit status can still include skipped models or failed runs. Keep partial artifacts and report which requested models or runs are missing. A failed run is not a zero-speed measurement.

## Interpret and compare

- Label the metric **generation throughput (tokens/sec)**: `eval_count / (eval_duration / 1e9)`. Loading, prompt processing, time to first token, total response time, and answer quality are outside this measurement.
- Report average, min/max, and successful runs out of requested runs for each model. Flag nonpositive or malformed metrics and unusually short generations. Use actual counts from `run.log`; the CSV's `tokens` field contains only the last successful run's count. A requested token budget and `stop: []` do not establish equal output lengths.
- Compare matching model digests, server hardware/version, prompt, and settings. State changed or unknown factors that limit a comparison. Across models, tokenizer differences also affect the meaning of tokens/sec.
- For a valid positive baseline, compute the throughput change as `(new_avg / old_avg - 1) * 100`. Show observed run ranges and avoid calling a small difference a reliable improvement when variation is similar in size. These summaries do not establish statistical significance.
- Summarize failures and limitations, then link the saved artifacts. Rerun only when requested or when a diagnosed execution problem justifies a bounded retry; preserve every attempt.

When a dashboard is requested, export selected run directories with `python3 scripts/export_dashboard.py results/RUN_DIRECTORY --output sites/flint-benchmarks/dist` from the repository root. The dashboard supports importing the resulting `benchmark.json`; read the README's dashboard section for local preview instructions. Follow the user's requested hosting scope when updating the dashboard.
