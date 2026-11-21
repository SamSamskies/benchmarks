---
name: local-model-eval
description: "Run real-world engineering evaluation tasks (coding, debugging, extraction, SQL, constraint planning, spec parsing) on installed Ollama models and update the Intelligence & Time per Task comparison dashboard. Use when asked to evaluate local models on real tasks, compare model intelligence vs latency, or add a new model to the benchmark dashboard."
---

# Local Model Evaluation & Dashboard

Evaluate installed Ollama models on the deterministic 6-task real-world suite (`evals/tasks.py`) and automatically update the Artificial Analysis–style dashboard (`dashboard/index.html`).

## Workflow

### 1. Check installed models and Ollama server

Confirm Ollama is running and identify exact installed model tags:

```bash
ollama list
```

### 2. Run the benchmark on one or more models

Execute `evals/runner.py` from the repository root (`python3 -m evals.runner` or `python3 evals/runner.py`):

```bash
python3 evals/runner.py <model-tag-1> [<model-tag-2> ...]
```

Useful flags:
- `--url URL`: Target Ollama endpoint (defaults to `$OLLAMA_HOST` or `http://127.0.0.1:11434`).
- `--timeout SECONDS`: Per-task wall-clock timeout (default `480`).
- `--num-predict TOKENS`: Max output/thinking tokens per task (default `16384`).
- `--num-ctx TOKENS`: Context size option (default `24576`).
- `--temperature FLOAT`: Override sampling temperature (omit to use each model's Ollama default).
- `--think LEVEL`: Optional Ollama `think` parameter (`low`, `medium`, `high`, `true`, `false`); omit to use each model's default.
- `--tasks id1,id2`: Re-run a subset of tasks and merge into `eval_results/<slug>.json`.
- `--no-rebuild`: Skip updating `dashboard/data.json` and `dashboard/data.js`.

Each model receives one short warmup call (excluded from task timings) and then runs all 6 tasks sequentially with `seed: 42` and model-default sampling.

### 3. Verify outputs and rebuild manually if needed

- Per-model raw results and check-level grading are saved under `eval_results/<model-slug>.json`.
- `evals/runner.py` automatically rebuilds `dashboard/data.json` and `dashboard/data.js` via:

```bash
python3 -m evals.build_dashboard
```

### 4. View the dashboard

Open `dashboard/index.html` directly or serve it locally:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory dashboard
```

Then open `http://127.0.0.1:8765` to view the **Intelligence** chart, **Time per Task** chart, **Efficiency Frontier** scatter plot, and **Task-by-Task Breakdown** modal inspector.

## Fair Comparison Rules

- Compare runs produced with the same `suite_version`, `seed: 42`, sampling configuration, `num_ctx`, `num_predict`, and host hardware.
- Report both **Intelligence Index** (0–100 across 60 total checks) and **Mean Time per Task (s)** (`total_duration - load_duration`). Note any truncated tasks (`done_reason == "length"`).
- Re-test graders after modifying `evals/tasks.py` with:

```bash
python3 -m evals.tasks --self-test
```
