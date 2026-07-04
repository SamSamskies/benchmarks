# Ollama Speed Benchmark

A dependency-light Bash CLI that measures the **generation speed (tokens/sec)** of your local Ollama models and prints a sorted comparison table.

## Requirements

- A running [Ollama](https://ollama.com) server (`ollama serve`)
- `curl` and `jq`
- `ollama` CLI (only needed for the `--all` flag)

## Quick start

```bash
./benchmark.sh qwen3:latest gemma3:latest
```

Benchmark every installed model and save a CSV:

```bash
./benchmark.sh --all -o results.csv
```

Use a model list file with more runs and a longer generation:

```bash
./benchmark.sh -f models.txt -n 5 -t 1000
```

## Options

| Option | Description | Default |
| --- | --- | --- |
| `-a`, `--all` | Benchmark every installed model (from `ollama list`) | off |
| `-f`, `--file FILE` | Read model names from `FILE` (one per line, `#` comments allowed) | - |
| `-p`, `--prompt TEXT` | Prompt to send | a fixed medium prompt |
| `-n`, `--runs N` | Timed runs per model to average | `3` |
| `-t`, `--tokens N` | Tokens to generate per run (`num_predict`) | `200` |
| `-u`, `--url URL` | Ollama host | `$OLLAMA_HOST` or `http://localhost:11434` |
| `-o`, `--output FILE` | Also write results as CSV | - |
| `-h`, `--help` | Show help | - |

Models can be passed as positional arguments, via `--file`, via `--all`, or any combination.

## How the metric works

For each request the script calls Ollama's `POST /api/generate` with `"stream": false`. The response includes:

- `eval_count` - number of tokens generated
- `eval_duration` - nanoseconds spent generating them

Generation speed is then:

```
tokens_per_sec = eval_count / (eval_duration / 1e9)
```

This isolates pure **decode throughput** - it excludes model load time and prompt processing, so it reflects how fast a model actually produces output.

### Why these choices

- **Warmup run.** Before the timed runs, one request is sent and discarded so the model is already loaded in memory. Cold-start load time never pollutes the measurement.
- **Fixed `options`.** Each request uses `seed: 42`, `temperature: 0`, `num_predict: <tokens>`, and `stop: []`. The seed/temperature make output deterministic, and `stop: []` prevents a model from ending early on an end-of-sequence token so every model generates the same length.
- **Token count (default 200).** More tokens lowers variance and amortizes per-request overhead for a steadier tok/s reading, at the cost of runtime. 200 is a fast default; raise it with `-t` (e.g. `-t 500` or `-t 1000`) for maximum stability.

## Example output

```
================ RESULTS (by tok/s) ================
MODEL          RUNS  AVG tok/s  MIN     MAX     TOKENS
gemma3:latest  3     84.21      82.10   86.55   500
qwen3:latest   3     61.40      60.02   62.88   500
```
