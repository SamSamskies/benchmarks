"""Run real-world benchmark tasks against local Ollama models and update the dashboard."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from evals import build_dashboard
from evals.tasks import COMMON_SYSTEM_PROMPT, SUITE_VERSION, TASK_BY_ID, TASKS, BenchmarkTask


RESULTS_DIR = ROOT_DIR / "eval_results"


def _normalize_url(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _http_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_think_option(raw: str | None) -> bool | str | None:
    if raw is None or raw == "":
        return None
    low = raw.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return raw.strip()


def _sysctl(key: str) -> str:
    try:
        out = subprocess.check_output(["sysctl", "-n", key], stderr=subprocess.DEVNULL, text=True)
        return out.strip()
    except Exception:
        return ""


def _collect_hardware() -> dict[str, Any]:
    mem_raw = _sysctl("hw.memsize")
    mem_gb = round(int(mem_raw) / (1024**3)) if mem_raw.isdigit() else None
    cpu = _sysctl("machdep.cpu.brand_string") or platform.processor() or platform.machine()
    cores = _sysctl("hw.ncpu")
    return {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "cpu": cpu,
        "cores": int(cores) if cores.isdigit() else os.cpu_count(),
        "memory_gb": mem_gb,
    }


def _slugify_model(model_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "__", model_name)


def _extract_inline_think(content: str, existing_thinking: str) -> tuple[str, str]:
    if existing_thinking:
        return content, existing_thinking
    matches = re.findall(r"<think>(.*?)</think>", content, flags=re.DOTALL | re.IGNORECASE)
    if not matches:
        return content, ""
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    return cleaned, "\n\n".join(m.strip() for m in matches if m.strip())


def _get_model_metadata(base_url: str, tag_entry: dict[str, Any], model_name: str) -> dict[str, Any]:
    details = tag_entry.get("details", {}) if isinstance(tag_entry, dict) else {}
    show_data: dict[str, Any] = {}
    try:
        show_data = _http_json(f"{base_url}/api/show", {"model": model_name}, timeout=15)
    except Exception:
        pass

    model_info = show_data.get("model_info", {}) if isinstance(show_data, dict) else {}
    architecture = model_info.get("general.architecture") or details.get("family") or ""
    context_length = None
    for k, v in model_info.items():
        if k.endswith(".context_length") and isinstance(v, int):
            context_length = v
            break

    size_bytes = tag_entry.get("size") if isinstance(tag_entry, dict) else None
    return {
        "digest": tag_entry.get("digest", "") if isinstance(tag_entry, dict) else "",
        "size_gb": round(size_bytes / 1e9, 1) if isinstance(size_bytes, int) and size_bytes > 0 else None,
        "parameter_size": details.get("parameter_size", ""),
        "quantization_level": details.get("quantization_level", ""),
        "architecture": architecture,
        "context_length": context_length,
        "capabilities": show_data.get("capabilities", []) if isinstance(show_data, dict) else [],
    }


def _run_warmup(base_url: str, model_name: str, num_ctx: int, timeout: int) -> float:
    t0 = time.monotonic()
    _http_json(
        f"{base_url}/api/chat",
        {
            "model": model_name,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            "options": {"seed": 42, "num_predict": 16, "num_ctx": num_ctx},
        },
        timeout=min(180, timeout),
    )
    return round(time.monotonic() - t0, 2)


def _run_task(
    base_url: str,
    model_name: str,
    task: BenchmarkTask,
    num_predict: int,
    num_ctx: int,
    temperature: float | None,
    think_option: bool | str | None,
    timeout: int,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "seed": 42,
        "num_predict": num_predict,
        "num_ctx": num_ctx,
    }
    if temperature is not None:
        options["temperature"] = temperature

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": COMMON_SYSTEM_PROMPT},
            {"role": "user", "content": task.prompt},
        ],
        "stream": True,
        "options": options,
    }
    if think_option is not None:
        payload["think"] = think_option

    req = Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    content_parts: list[str] = []
    thinking_parts: list[str] = []
    final_meta: dict[str, Any] = {}
    timed_out = False
    chunk_count = 0
    socket_timeout = min(120, timeout)

    t0 = time.monotonic()
    try:
        with urlopen(req, timeout=socket_timeout) as resp:
            for raw_line in resp:
                if time.monotonic() - t0 > timeout:
                    timed_out = True
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                obj = json.loads(line)
                msg = obj.get("message") or {}
                if msg.get("thinking"):
                    thinking_parts.append(msg["thinking"])
                if msg.get("content"):
                    content_parts.append(msg["content"])
                chunk_count += 1
                if obj.get("done"):
                    final_meta = obj
                    break
    except (TimeoutError, socket.timeout):
        timed_out = True

    wall_s = time.monotonic() - t0
    raw_content = "".join(content_parts)
    raw_thinking = "".join(thinking_parts)
    content, thinking = _extract_inline_think(raw_content, raw_thinking)

    total_ns = int(final_meta.get("total_duration") or 0)
    load_ns = int(final_meta.get("load_duration") or 0)
    eval_ns = int(final_meta.get("eval_duration") or 0)
    prompt_eval_ns = int(final_meta.get("prompt_eval_duration") or 0)
    eval_count = int(final_meta.get("eval_count") or chunk_count)
    prompt_eval_count = int(final_meta.get("prompt_eval_count") or 0)

    if total_ns > 0:
        inference_time_s = round(max(0, total_ns - load_ns) / 1e9, 2)
    else:
        inference_time_s = round(wall_s, 2)

    if eval_ns > 0 and eval_count > 0:
        tokens_per_sec = round(eval_count / (eval_ns / 1e9), 1)
    elif wall_s > 0 and eval_count > 0:
        tokens_per_sec = round(eval_count / wall_s, 1)
    else:
        tokens_per_sec = 0.0

    done_reason = "timeout" if timed_out else (final_meta.get("done_reason") or "")
    grade = task.grade(raw_content)

    return {
        "id": task.id,
        "title": task.title,
        "short_label": task.short_label,
        "category": task.category,
        "inference_time_s": inference_time_s,
        "wall_time_s": round(wall_s, 2),
        "load_time_s": round(load_ns / 1e9, 2),
        "prompt_eval_time_s": round(prompt_eval_ns / 1e9, 2),
        "eval_time_s": round(eval_ns / 1e9, 2),
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "tokens_per_sec": tokens_per_sec,
        "done_reason": done_reason,
        "thinking": thinking,
        "content": content,
        "grade": grade,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the real-world benchmark suite on local Ollama models and update the dashboard."
    )
    parser.add_argument("models", nargs="+", help="Ollama model names (e.g. qwen3.8:27b-mlx)")
    parser.add_argument(
        "--url",
        default=os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434",
        help="Ollama base URL (default: $OLLAMA_HOST or http://127.0.0.1:11434)",
    )
    parser.add_argument("--timeout", type=int, default=900, help="Per-task wall-clock timeout in seconds (default: 900)")
    parser.add_argument("--tasks", default="", help="Optional comma-separated subset of task IDs to run")
    parser.add_argument("--num-predict", type=int, default=24576, help="Max generation tokens per task (default: 24576)")
    parser.add_argument("--num-ctx", type=int, default=32768, help="Context length option (default: 32768)")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional sampling temperature override. Omit to use model default.",
    )
    parser.add_argument(
        "--think",
        default=None,
        help="Optional Ollama think parameter (e.g. medium, high, low, true, false). Omit to use model default.",
    )
    parser.add_argument("--no-rebuild", action="store_true", help="Skip rebuilding dashboard/data.{json,js}")
    args = parser.parse_args(argv)

    base_url = _normalize_url(args.url)
    think_option = _parse_think_option(args.think)

    selected_tasks: list[BenchmarkTask]
    if args.tasks.strip():
        ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
        missing_tasks = [tid for tid in ids if tid not in TASK_BY_ID]
        if missing_tasks:
            print(f"Error: unknown task ID(s): {', '.join(missing_tasks)}", file=sys.stderr)
            print(f"Available tasks: {', '.join(t.id for t in TASKS)}", file=sys.stderr)
            return 1
        selected_tasks = [TASK_BY_ID[tid] for tid in ids]
    else:
        selected_tasks = list(TASKS)

    try:
        version_resp = _http_json(f"{base_url}/api/version", timeout=10)
        tags_resp = _http_json(f"{base_url}/api/tags", timeout=10)
    except (URLError, HTTPError, TimeoutError, OSError) as exc:
        print(f"Error: could not connect to Ollama at {base_url}: {exc}", file=sys.stderr)
        return 1

    ollama_version = version_resp.get("version", "unknown")
    installed_map = {m["name"]: m for m in tags_resp.get("models", []) if "name" in m}

    missing_models = [m for m in args.models if m not in installed_map]
    if missing_models:
        print(f"Error: model(s) not installed on {base_url}: {', '.join(missing_models)}", file=sys.stderr)
        print("Run `ollama list` to view available model tags.", file=sys.stderr)
        return 1

    hardware = _collect_hardware()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Ollama v{ollama_version} @ {base_url} | Suite v{SUITE_VERSION} ({len(selected_tasks)} tasks)")

    for model_name in args.models:
        print(f"\n=== Evaluating {model_name} ===")
        model_info = _get_model_metadata(base_url, installed_map[model_name], model_name)

        try:
            warmup_s = _run_warmup(base_url, model_name, num_ctx=args.num_ctx, timeout=args.timeout)
            print(f"  Warmup ready in {warmup_s:.2f}s (num_ctx={args.num_ctx})")
        except Exception as exc:
            print(f"  Error during warmup for {model_name}: {exc}", file=sys.stderr)
            return 1

        task_results: list[dict[str, Any]] = []
        for idx, task in enumerate(selected_tasks, start=1):
            try:
                res = _run_task(
                    base_url=base_url,
                    model_name=model_name,
                    task=task,
                    num_predict=args.num_predict,
                    num_ctx=args.num_ctx,
                    temperature=args.temperature,
                    think_option=think_option,
                    timeout=args.timeout,
                )
            except Exception as exc:
                print(f"  [{idx}/{len(selected_tasks)}] {task.id:<24} ERROR: {exc}", file=sys.stderr)
                return 1

            g = res["grade"]
            flag = ""
            if res["done_reason"] == "length":
                flag = " [TRUNCATED]"
            elif res["done_reason"] == "timeout":
                flag = " [TIMEOUT]"
            print(
                f"  [{idx}/{len(selected_tasks)}] {task.id:<24} "
                f"{g['score']:5.1f}/100 ({g['passed_checks']:2d}/{g['total_checks']})  "
                f"{res['inference_time_s']:6.2f}s  {res['tokens_per_sec']:5.1f} tok/s  "
                f"({res['eval_count']} tok){flag}"
            )
            task_results.append(res)

        out_path = RESULTS_DIR / f"{_slugify_model(model_name)}.json"
        if args.tasks.strip() and out_path.exists():
            with out_path.open("r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_map = {t["id"]: t for t in existing.get("tasks", [])}
            for item in task_results:
                existing_map[item["id"]] = item
            task_results = [existing_map[t.id] for t in TASKS if t.id in existing_map]

        scores = [t["grade"]["score"] for t in task_results]
        times = [t["inference_time_s"] for t in task_results]
        intelligence = round(sum(scores) / len(scores), 1) if scores else 0.0
        mean_time = round(sum(times) / len(times), 2) if times else 0.0

        options_record: dict[str, Any] = {
            "seed": 42,
            "temperature": args.temperature if args.temperature is not None else "default",
            "num_predict": args.num_predict,
            "num_ctx": args.num_ctx,
        }
        if think_option is not None:
            options_record["think"] = think_option

        record = {
            "model": model_name,
            "evaluated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "suite_version": SUITE_VERSION,
            "ollama_url": base_url,
            "ollama_version": ollama_version,
            "hardware": hardware,
            "options": options_record,
            "warmup_s": warmup_s,
            "model_info": model_info,
            "summary": {
                "intelligence_index": intelligence,
                "mean_time_per_task_s": mean_time,
                "total_time_s": round(sum(times), 2),
                "tasks_completed": len(task_results),
            },
            "tasks": task_results,
        }

        with out_path.open("w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.write("\n")

        print(
            f"  => Intelligence: {intelligence:.1f}/100 | Mean Time/Task: {mean_time:.2f}s | Saved: {out_path.name}"
        )

    if not args.no_rebuild:
        payload = build_dashboard.build()
        print(f"\nDashboard data updated ({len(payload['models'])} total models in dashboard/data.json).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
