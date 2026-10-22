window.BENCHMARK_DATA = {
  "schemaVersion": 1,
  "runs": [
    {
      "id": "20261022T014306Z-flint-qIA05N",
      "startedAt": "2026-10-22T01:43:41.131021Z",
      "finishedAt": "2026-10-22T01:43:57.941774Z",
      "status": "complete",
      "exitCode": 0,
      "model": {
        "name": "flint:latest",
        "digest": "4a4aceea54906e4a49e6b59a8a0c625e6f890427f9d8630e4d63af41514bba49",
        "sizeBytes": 22579888661,
        "runner": "mlx",
        "quantization": "int4"
      },
      "server": {
        "version": "0.40.8"
      },
      "hardware": {
        "model": "Mac16,11",
        "chip": "Apple M4 Pro",
        "cpuCores": 12,
        "memoryBytes": 68719476736,
        "os": "macOS 26.6.2",
        "gpu": [
          {
            "sppci_model": "Apple M4 Pro",
            "sppci_cores": "16"
          }
        ]
      },
      "configuration": {
        "runsRequested": 3,
        "tokenBudget": 200,
        "warmupRuns": 1,
        "prompt": "Write a detailed explanation of how the water cycle works, including evaporation, condensation, precipitation, and collection. Cover each stage thoroughly.",
        "seed": 42,
        "temperature": 0,
        "stop": [],
        "otherOptions": "Ollama and model defaults"
      },
      "source": {
        "revision": "0a581df5464e72da9b4fe246c1dad0e8b55d6d1e",
        "scriptSha256": "9c107419ef4d5ebed10f32db0ab600b24f52470ff5570fa519aa74b2c0de41e2",
        "dirty": true
      },
      "runtime": {
        "contextLength": 262144,
        "vramBytes": 22629092944
      },
      "samples": [
        {
          "index": 1,
          "status": "success",
          "tokensPerSecond": 103.83,
          "tokens": 200
        },
        {
          "index": 2,
          "status": "success",
          "tokensPerSecond": 105.08,
          "tokens": 200
        },
        {
          "index": 3,
          "status": "success",
          "tokensPerSecond": 101.53,
          "tokens": 200
        }
      ],
      "stats": {
        "average": 103.48,
        "min": 101.53,
        "max": 105.08
      },
      "files": {
        "csv": "model,runs,avg_tokens_per_sec,min_tokens_per_sec,max_tokens_per_sec,tokens\nflint:latest,3,103.48,101.53,105.08,200\n",
        "log": "Ollama speed benchmark @ http://localhost:11434\nruns=3  tokens=200  models=1\n\n== flint:latest ==\n  warmup... done\n  run 1/3: 103.83 tok/s (200 tokens)\n  run 2/3: 105.08 tok/s (200 tokens)\n  run 3/3: 101.53 tok/s (200 tokens)\n  avg: 103.48 tok/s\n\n================ RESULTS (by tok/s) ================\nMODEL         RUNS  AVG tok/s  MIN     MAX     TOKENS\nflint:latest  3     103.48     101.53  105.08  200\n\nCSV written to /Users/samsamskies/Workspace/benchmarks/results/20261022T014306Z-flint-qIA05N/results.csv\n",
        "context": "# Flint generation-speed benchmark\n\n- Started (UTC): 2026-10-22T01:43:41.131021Z\n- Finished (UTC): 2026-10-22T01:43:57.941774Z\n- Status: completed\n- Exit status: 0\n- Server: http://localhost:11434\n- Ollama version: 0.40.8\n- Model: flint:latest\n- Digest: 4a4aceea54906e4a49e6b59a8a0c625e6f890427f9d8630e4d63af41514bba49\n- Runner: mlx\n- Quantization: int4\n- Hardware: {\"model\": \"Mac16,11\", \"chip\": \"Apple M4 Pro\", \"cpuCores\": 12, \"memoryBytes\": 68719476736, \"os\": \"macOS 26.6.2\", \"gpu\": [{\"sppci_model\": \"Apple M4 Pro\", \"sppci_cores\": \"16\"}]}\n- Repository revision: 0a581df5464e72da9b4fe246c1dad0e8b55d6d1e\n- Script SHA-256: 9c107419ef4d5ebed10f32db0ab600b24f52470ff5570fa519aa74b2c0de41e2\n- Timed runs: 3\n- Token budget per run: 200\n- Warmup runs: 1\n- Options: seed=42, temperature=0, stop=[]; remaining options use server/model defaults\n\n## Command\n\n```json\n[\n  \"./benchmark.sh\",\n  \"--url\",\n  \"http://localhost:11434\",\n  \"--runs\",\n  \"3\",\n  \"--tokens\",\n  \"200\",\n  \"--output\",\n  \"/Users/samsamskies/Workspace/benchmarks/results/20261022T014306Z-flint-qIA05N/results.csv\",\n  \"flint:latest\"\n]\n```\n\n## Prompt\n\nWrite a detailed explanation of how the water cycle works, including evaporation, condensation, precipitation, and collection. Cover each stage thoroughly.\n\n## Working-tree state before run\n\n```text\nM README.md\n?? .agents/\n?? .gitignore\n```\n\nGeneration throughput uses eval_count / (eval_duration / 1e9). Load and prompt-processing time are excluded. The token budget is not a guarantee of output length.\n"
      }
    }
  ]
};
