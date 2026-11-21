# Local Model Benchmarks

This repository provides two local Ollama evaluation tools:

1. **Real-World Task Benchmark & Dashboard (`evals/runner.py`)** — evaluates installed models across 6 deterministic engineering tasks (**Intelligence Index**) alongside **Time per Task**, visualized on an interactive Artificial Analysis–style dashboard (`dashboard/index.html`).
2. **Ollama Speed Benchmark (`benchmark.sh`)** — a lightweight Bash CLI measuring pure decode throughput (tokens/sec).

---

## Real-World Task Benchmark & Dashboard

Measures both accuracy and inference latency across **6 realistic tasks** (10 automated checks each, 60 total checks):

| Task ID | Label | Category | What It Tests |
| --- | --- | --- | --- |
| `code-rate-limiter` | Code | Algorithms & State | `TokenBucketLimiter` & `SlidingWindowLimiter` with atomic multi-cost, expiry boundaries, `retry_after`, and `reset` |
| `debug-csv-aggregator` | Debug | Root-Cause Debugging | Fixing 5 bugs in a multi-currency ledger (`Decimal` rounding, UTC timezones, idempotency deduplication, refunds, numeric sort) |
| `extract-incident-report` | Extract | Structured Extraction | Turning noisy Slack/PagerDuty/email logs into UTC-normalized JSON without disproven hypotheses or PII |
| `sql-saas-analytics` | SQL | Relational Analytics | Fan-out-safe SQLite query across `accounts`, `invoices`, and `usage_events` with zero-division handling |
| `logic-release-schedule` | Planning | Constraint Satisfaction | Optimal 6-service deployment schedule under DAG, skill, availability, freeze-window, and concurrency constraints |
| `spec-log-parser` | Parser | Spec Implementation | Reverse-proxy log parser handling IPv6 brackets, escaped quotes, `-` nulls, quoted metadata, and invalid-line rejection |

### Quick start

Run one or more installed models (`ollama list`):

```bash
python3 evals/runner.py inkling-2-mini:26b-a5b-nvfp4 qwen3.8:27b-mlx
```

Add another model later and automatically update the dashboard:

```bash
python3 evals/runner.py gemma4:26b-mlx
```

Results are saved per model in `eval_results/<model-slug>.json`, and `dashboard/data.json` + `dashboard/data.js` are rebuilt automatically.

### Runner options

| Option | Description | Default |
| --- | --- | --- |
| `--url URL` | Ollama server URL | `$OLLAMA_HOST` or `http://127.0.0.1:11434` |
| `--timeout SECONDS` | Per-task wall-clock deadline | `900` |
| `--num-predict TOKENS` | Max output/thinking tokens per task | `24576` |
| `--num-ctx TOKENS` | Context window size | `32768` |
| `--temperature FLOAT` | Override sampling temperature | model default |
| `--think LEVEL` | Override Ollama `think` setting (`low`, `medium`, `high`, `true`, `false`) | model default |
| `--tasks id1,id2` | Re-run a subset of tasks and merge into the model's saved JSON | all 6 tasks |
| `--no-rebuild` | Skip regenerating `dashboard/data.{json,js}` | off |

### Viewing the dashboard

Open `dashboard/index.html` directly, or serve it locally:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory dashboard
```

Then visit `http://127.0.0.1:8765` to inspect:
- **Intelligence** and **Time per Task** charts (with per-task scope filtering)
- **Speed vs. Intelligence Frontier** scatter plot
- **Task-by-Task Breakdown** sortable table with a click-to-inspect modal showing every automated assertion, model output, thinking trace, and prompt

### Agent skill & grader self-test

- A reusable skill lives in [`.agents/skills/local-model-eval`](.agents/skills/local-model-eval/SKILL.md).
- Verify all 6 graders against their reference solutions before running models:

```bash
python3 -m evals.tasks --self-test
```

---

## Ollama Speed Benchmark (`benchmark.sh`)

A dependency-light Bash CLI that measures the **generation speed (tokens/sec)** of your local Ollama models and prints a sorted comparison table.

### Requirements

- A running [Ollama](https://ollama.com) server (`ollama serve`)
- `curl` and `jq`
- `ollama` CLI (only needed for the `--all` flag)

### Quick start

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

### Options

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

### Legacy Codex skill (`ollama-benchmark`)

This repository also includes an `ollama-benchmark` skill in
[`.agents/skills/ollama-benchmark`](.agents/skills/ollama-benchmark/SKILL.md) for running `benchmark.sh` and exporting CSV/throughput runs to `sites/flint-benchmarks/dist/`.

### How the throughput metric works

For each request `benchmark.sh` calls Ollama's `POST /api/generate` with `"stream": false`:

```
tokens_per_sec = eval_count / (eval_duration / 1e9)
```

This isolates pure **decode throughput** — excluding model load time and prompt processing.

#### Why these choices

- **Warmup run.** Before the timed runs, one request is sent and discarded so the model is already loaded in memory. Cold-start load time never pollutes the measurement.
- **Fixed `options`.** Each request uses `seed: 42`, `temperature: 0`, `num_predict: <tokens>`, and `stop: []`. These keep the requested settings consistent; use the actual token counts printed for each run when comparing output lengths.
- **Token count (default 200).** More tokens lowers variance and amortizes per-request overhead for a steadier tok/s reading, at the cost of runtime. 200 is a fast default; raise it with `-t` (e.g. `-t 500` or `-t 1000`) for maximum stability.
