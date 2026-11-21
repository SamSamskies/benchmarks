"""Compile eval_results/*.json into dashboard/data.json and dashboard/data.js."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from evals.tasks import SUITE_VERSION, TASKS

RESULTS_DIR = ROOT_DIR / "eval_results"
DASHBOARD_DIR = ROOT_DIR / "dashboard"


_FAMILY_PROFILES: list[tuple[str, dict[str, str]]] = [
    ("inkling", {"family": "inkling", "label": "Inkling", "monogram": "IN", "color": "#c27656"}),
    ("qwen", {"family": "qwen", "label": "Qwen", "monogram": "QW", "color": "#f96b08"}),
    ("gemma", {"family": "gemma", "label": "Gemma", "monogram": "G", "color": "#2d8a4e"}),
    ("deepseek", {"family": "deepseek", "label": "DeepSeek", "monogram": "DS", "color": "#1749eb"}),
    ("nemotron", {"family": "nemotron", "label": "Nemotron", "monogram": "NV", "color": "#16a34a"}),
    ("granite", {"family": "granite", "label": "Granite", "monogram": "GR", "color": "#0f766e"}),
    ("gpt-oss", {"family": "gpt-oss", "label": "GPT-OSS", "monogram": "GP", "color": "#1f242b"}),
    ("minimax", {"family": "minimax", "label": "MiniMax", "monogram": "MM", "color": "#e11d48"}),
    ("muse", {"family": "muse", "label": "Muse", "monogram": "MU", "color": "#0284c7"}),
    ("flint", {"family": "flint", "label": "Flint", "monogram": "FL", "color": "#6d4aff"}),
    ("ornith", {"family": "ornith", "label": "Ornith", "monogram": "OR", "color": "#7c5cdb"}),
    ("laguna", {"family": "laguna", "label": "Laguna", "monogram": "LG", "color": "#0891b2"}),
]

_FALLBACK_COLORS = ["#6d4aff", "#0284c7", "#0d9488", "#be123c", "#9333ea", "#ca8a04"]


def _resolve_brand(model_name: str, architecture: str = "") -> dict[str, str]:
    haystack = f"{model_name} {architecture}".lower()
    for needle, profile in _FAMILY_PROFILES:
        if needle in haystack:
            return dict(profile)
    base = model_name.split(":")[0].split("/")[-1]
    letters = "".join(ch for ch in base if ch.isalpha()).upper()
    monogram = (letters[:2] or "LM").ljust(2, "X")[:2]
    digest_idx = int(hashlib.sha256(base.encode("utf-8")).hexdigest()[:6], 16) % len(_FALLBACK_COLORS)
    return {
        "family": base.lower(),
        "label": base,
        "monogram": monogram,
        "color": _FALLBACK_COLORS[digest_idx],
    }


def _split_model_name(model_name: str) -> tuple[str, str]:
    if ":" in model_name:
        base, tag = model_name.split(":", 1)
        return base, tag
    return model_name, ""


def _normalize_run(raw: dict[str, Any], source_file: str) -> dict[str, Any]:
    model_name = raw["model"]
    info = raw.get("model_info", {})
    brand = _resolve_brand(model_name, info.get("architecture", ""))
    base_name, tag_name = _split_model_name(model_name)

    tasks_list = raw.get("tasks", [])
    tasks_by_id = {t["id"]: t for t in tasks_list}

    ordered_tasks = []
    for task_def in TASKS:
        item = tasks_by_id.get(task_def.id)
        if item is not None:
            ordered_tasks.append(item)

    scores = [float(t.get("grade", {}).get("score", 0.0)) for t in ordered_tasks]
    times = [float(t.get("inference_time_s", 0.0)) for t in ordered_tasks]
    tps_vals = [float(t.get("tokens_per_sec", 0.0)) for t in ordered_tasks if t.get("tokens_per_sec", 0.0) > 0]

    intelligence = round(sum(scores) / len(scores), 1) if scores else 0.0
    mean_time_s = round(sum(times) / len(times), 2) if times else 0.0
    total_time_s = round(sum(times), 2)
    mean_tps = round(sum(tps_vals) / len(tps_vals), 1) if tps_vals else 0.0
    total_tokens = sum(int(t.get("eval_count", 0)) for t in ordered_tasks)
    perfect_count = sum(1 for s in scores if s >= 100.0)

    return {
        "model": model_name,
        "base_name": base_name,
        "tag_name": tag_name,
        "brand": brand,
        "source_file": source_file,
        "evaluated_at_utc": raw.get("evaluated_at_utc", ""),
        "suite_version": raw.get("suite_version", SUITE_VERSION),
        "ollama_version": raw.get("ollama_version", ""),
        "hardware": raw.get("hardware", {}),
        "options": raw.get("options", {}),
        "model_info": info,
        "warmup_s": raw.get("warmup_s", 0.0),
        "summary": {
            "intelligence_index": intelligence,
            "mean_time_per_task_s": mean_time_s,
            "total_time_s": total_time_s,
            "mean_tokens_per_sec": mean_tps,
            "total_eval_tokens": total_tokens,
            "tasks_completed": len(ordered_tasks),
            "tasks_total": len(TASKS),
            "tasks_perfect": perfect_count,
        },
        "tasks": ordered_tasks,
    }


def build(results_dir: Path = RESULTS_DIR, dashboard_dir: Path = DASHBOARD_DIR) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    models: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict) or "model" not in raw or "tasks" not in raw:
            continue
        models.append(_normalize_run(raw, path.name))

    models.sort(
        key=lambda m: (
            -m["summary"]["intelligence_index"],
            m["summary"]["mean_time_per_task_s"],
            m["model"],
        )
    )

    task_catalog = [
        {
            "id": t.id,
            "title": t.title,
            "short_label": t.short_label,
            "category": t.category,
            "description": t.description,
            "prompt": t.prompt,
        }
        for t in TASKS
    ]

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "suite_version": SUITE_VERSION,
        "tasks": task_catalog,
        "models": models,
    }

    data_json_path = dashboard_dir / "data.json"
    data_js_path = dashboard_dir / "data.js"

    with data_json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    with data_js_path.open("w", encoding="utf-8") as f:
        f.write("// Auto-generated by evals/build_dashboard.py — do not edit manually.\n")
        f.write("window.BENCHMARK_DATA = ")
        json.dump(payload, f, indent=2)
        f.write(";\n")

    return payload


if __name__ == "__main__":
    result = build()
    print(f"Built dashboard data with {len(result['models'])} model(s) -> {DASHBOARD_DIR}")
