# Saved run metadata

The dashboard exporter reads one model per run directory. Save `metadata.json`
alongside `context.md`, `run.log`, and the script's `results.csv`. This structure
keeps the settings and machine information separate from the measured log/CSV.

```json
{
  "schemaVersion": 1,
  "id": "unique-run-directory-name",
  "startedAt": "2026-10-22T01:43:41Z",
  "finishedAt": "2026-10-22T01:43:58Z",
  "exitCode": 0,
  "model": {
    "name": "flint:latest",
    "digest": "full digest from the target server's /api/tags",
    "size": null,
    "details": {"runner": "mlx", "quantization_level": "int4"}
  },
  "server": {"url": "http://localhost:11434", "version": "0.40.8"},
  "hardware": {
    "chip": "Apple M4 Pro",
    "memoryBytes": 68719476736,
    "os": "macOS 26.6.2",
    "gpu": [{"sppci_cores": "16"}]
  },
  "benchmark": {
    "runsRequested": 3,
    "tokenBudget": 200,
    "warmupRuns": 1,
    "prompt": "The exact prompt sent to the model",
    "seed": 42,
    "temperature": 0,
    "stop": [],
    "otherOptions": "Ollama and model defaults"
  },
  "source": {
    "revision": "repository commit SHA",
    "workingTree": "git status --short output before running; empty when clean",
    "scriptSha256": "SHA-256 of the script that executed"
  }
}
```

These are illustrative metadata fields; obtain actual values for every new run.
Use UTC ISO timestamps and a unique ID. Write initial context before execution,
then save the finish time and actual process exit code afterward. Hardware must
describe the server; use an empty object for unknown hardware. Additional fields
are allowed. An optional `loadedModelsAfter` snapshot from `/api/ps` supplies the
loaded context length and reported device allocation.

Never fabricate timed samples or replace failures with zero speed. The exporter
reads per-run throughput and token counts from `run.log` and verifies successful
counts, average, minimum, and maximum against the CSV. Failed or missing timed
runs appear as partial results. The source log contains speeds rounded by the
benchmark script, so the dashboard retains that precision.
