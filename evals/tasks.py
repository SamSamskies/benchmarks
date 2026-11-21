"""Real-world benchmark task suite with deterministic automated graders."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from typing import Any, Callable


SUITE_VERSION = "1.0.0"

COMMON_SYSTEM_PROMPT = (
    "You are being evaluated on a real-world engineering task. "
    "Follow all specifications, schemas, edge cases, and output format requirements precisely."
)


def strip_thinking_blocks(text: str) -> str:
    """Remove inline <think>...</think> tags if a runtime embeds reasoning in content."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def extract_code_block(text: str, preferred_langs: tuple[str, ...] = ("python", "py")) -> str:
    """Extract the most relevant fenced code block, or return cleaned raw text."""
    cleaned = strip_thinking_blocks(text)
    pattern = re.compile(r"```([a-zA-Z0-9_+-]*)\s*\n(.*?)```", flags=re.DOTALL)
    matches = pattern.findall(cleaned)
    if not matches:
        return cleaned.strip()
    preferred_lower = {lang.lower() for lang in preferred_langs}
    for lang, body in reversed(matches):
        if lang.lower() in preferred_lower:
            return body.strip()
    return matches[-1][1].strip()


def extract_json_payload(text: str) -> tuple[Any | None, str, str | None]:
    """Extract and parse JSON from response text (fenced or raw)."""
    cleaned = strip_thinking_blocks(text)
    candidates: list[str] = []

    fence_matches = re.findall(r"```(?:json)?\s*\n(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    for match in reversed(fence_matches):
        candidates.append(match.strip())

    candidates.append(cleaned)

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(cleaned[first_brace : last_brace + 1])

    last_error: str | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate), candidate, None
        except json.JSONDecodeError as exc:
            last_error = str(exc)

    return None, cleaned, last_error or "Could not locate valid JSON object"


def _run_python_tests(code: str, test_script: str, check_names: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Write extracted module + unit test runner to a temp dir and execute isolated."""
    if not code.strip():
        return [
            {"name": name, "passed": False, "detail": "No Python code produced"}
            for name in check_names
        ], "Empty code"

    with tempfile.TemporaryDirectory(prefix="llm_eval_") as tmpdir:
        solution_path = os.path.join(tmpdir, "solution.py")
        runner_path = os.path.join(tmpdir, "run_checks.py")
        with open(solution_path, "w", encoding="utf-8") as f:
            f.write(code + "\n")
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(test_script)

        try:
            proc = subprocess.run(
                [sys.executable, "-I", runner_path],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return [
                {"name": name, "passed": False, "detail": "Execution timed out (10s)"}
                for name in check_names
            ], "Timed out after 10s"

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        marker = "__EVAL_RESULT__:"
        for line in reversed(stdout.splitlines()):
            if line.startswith(marker):
                payload = json.loads(line[len(marker) :])
                return payload["checks"], ""

        err_msg = stderr or stdout or f"Process exited with code {proc.returncode}"
        err_short = err_msg.splitlines()[-1][:240] if err_msg else "Execution failed"
        return [
            {"name": name, "passed": False, "detail": f"Import/runtime error: {err_short}"}
            for name in check_names
        ], err_msg


def _build_grade_result(checks: list[dict[str, Any]], extracted: str) -> dict[str, Any]:
    total = len(checks)
    passed = sum(1 for c in checks if c["passed"])
    score = round((passed / total) * 100.0, 1) if total else 0.0
    return {
        "score": score,
        "passed_checks": passed,
        "total_checks": total,
        "checks": checks,
        "extracted": extracted,
    }


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    title: str
    short_label: str
    category: str
    description: str
    prompt: str
    grader: Callable[[str], dict[str, Any]]
    golden_solution: str

    def grade(self, response_text: str) -> dict[str, Any]:
        return self.grader(response_text)


# ---------------------------------------------------------------------------
# Task 1: Rate Limiter Library (Code & State Machines)
# ---------------------------------------------------------------------------

_TASK1_CHECKS = [
    "TokenBucket initial burst",
    "TokenBucket exhaust and deny",
    "TokenBucket fractional refill",
    "TokenBucket cap at capacity",
    "TokenBucket atomic multi-cost",
    "TokenBucket retry_after accuracy",
    "TokenBucket cost > capacity & reset",
    "SlidingWindow exact boundary",
    "SlidingWindow per-key isolation & remaining",
    "SlidingWindow retry_after & reset",
]

_TASK1_TEST_RUNNER = textwrap.dedent(
    """
    import json
    import math
    import sys

    sys.path.insert(0, ".")
    checks = []

    def record(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "passed": True, "detail": detail or "ok"})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    import solution
    TokenBucketLimiter = solution.TokenBucketLimiter
    SlidingWindowLimiter = solution.SlidingWindowLimiter

    class FakeClock:
        def __init__(self, t=100.0):
            self.t = t
        def __call__(self):
            return self.t
        def advance(self, dt):
            self.t += dt

    def t1():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=5, refill_rate=2.0, now_fn=clk)
        results = [tb.allow("u1") for _ in range(5)]
        assert results == [True] * 5, f"Expected 5 True, got {results}"
        assert abs(tb.remaining("u1") - 0.0) < 1e-6

    def t2():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=3, refill_rate=1.0, now_fn=clk)
        assert [tb.allow("u1") for _ in range(4)] == [True, True, True, False]

    def t3():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=4, refill_rate=2.0, now_fn=clk)
        assert tb.allow("u1", cost=4) is True
        clk.advance(0.75)
        assert abs(tb.remaining("u1") - 1.5) < 1e-6
        assert tb.allow("u1", cost=1) is True
        assert abs(tb.remaining("u1") - 0.5) < 1e-6
        assert tb.allow("u1", cost=1) is False

    def t4():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=5, refill_rate=10.0, now_fn=clk)
        assert tb.allow("u1", cost=3) is True
        clk.advance(100.0)
        assert abs(tb.remaining("u1") - 5.0) < 1e-6

    def t5():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=5, refill_rate=1.0, now_fn=clk)
        assert tb.allow("u1", cost=3) is True
        assert tb.allow("u1", cost=3) is False
        assert abs(tb.remaining("u1") - 2.0) < 1e-6, "Failed allow deducted tokens"

    def t6():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=6, refill_rate=2.0, now_fn=clk)
        assert tb.allow("u1", cost=5) is True
        wait = tb.retry_after("u1", cost=4)
        assert abs(wait - 1.5) < 1e-6, f"Expected 1.5s, got {wait}"
        assert abs(tb.retry_after("u1", cost=1) - 0.0) < 1e-6

    def t7():
        clk = FakeClock()
        tb = TokenBucketLimiter(capacity=4, refill_rate=2.0, now_fn=clk)
        assert tb.allow("u1", cost=5) is False
        assert math.isinf(tb.retry_after("u1", cost=5))
        assert tb.allow("u1", cost=4) is True
        assert tb.allow("u2", cost=1) is True
        tb.reset("u1")
        assert abs(tb.remaining("u1") - 4.0) < 1e-6
        assert abs(tb.remaining("u2") - 3.0) < 1e-6
        tb.reset()
        assert abs(tb.remaining("u2") - 4.0) < 1e-6

    def t8():
        clk = FakeClock(t=10.0)
        sw = SlidingWindowLimiter(limit=3, window_seconds=5.0, now_fn=clk)
        assert sw.allow("k1") is True
        clk.advance(2.0)
        assert sw.allow("k1", cost=2) is True
        assert sw.allow("k1") is False
        clk.advance(2.9)
        assert sw.allow("k1") is False, "Expired too early at dt=4.9"
        clk.advance(0.1)
        assert sw.allow("k1") is True, "Did not expire at exact boundary dt=5.0"
        assert sw.allow("k1") is False

    def t9():
        clk = FakeClock(t=0.0)
        sw = SlidingWindowLimiter(limit=4, window_seconds=10.0, now_fn=clk)
        assert sw.allow("a", cost=3) is True
        assert sw.remaining("a") == 1
        assert sw.remaining("b") == 4
        assert sw.allow("a", cost=2) is False
        assert sw.remaining("a") == 1
        assert sw.allow("a", cost=5) is False

    def t10():
        clk = FakeClock(t=0.0)
        sw = SlidingWindowLimiter(limit=3, window_seconds=10.0, now_fn=clk)
        assert sw.allow("a") is True
        clk.advance(3.0)
        assert sw.allow("a", cost=2) is True
        wait1 = sw.retry_after("a", cost=1)
        assert abs(wait1 - 7.0) < 1e-6, f"Expected 7.0s wait, got {wait1}"
        wait2 = sw.retry_after("a", cost=2)
        assert abs(wait2 - 10.0) < 1e-6, f"Expected 10.0s wait, got {wait2}"
        assert math.isinf(sw.retry_after("a", cost=4))
        sw.reset("a")
        assert sw.remaining("a") == 3

    tests = [
        ("TokenBucket initial burst", t1),
        ("TokenBucket exhaust and deny", t2),
        ("TokenBucket fractional refill", t3),
        ("TokenBucket cap at capacity", t4),
        ("TokenBucket atomic multi-cost", t5),
        ("TokenBucket retry_after accuracy", t6),
        ("TokenBucket cost > capacity & reset", t7),
        ("SlidingWindow exact boundary", t8),
        ("SlidingWindow per-key isolation & remaining", t9),
        ("SlidingWindow retry_after & reset", t10),
    ]
    for name, fn in tests:
        record(name, fn)

    print("__EVAL_RESULT__:" + json.dumps({"checks": checks}))
    """
)


def _grade_task1(response_text: str) -> dict[str, Any]:
    code = extract_code_block(response_text, ("python", "py"))
    checks, _ = _run_python_tests(code, _TASK1_TEST_RUNNER, _TASK1_CHECKS)
    return _build_grade_result(checks, code)


_TASK1_PROMPT = textwrap.dedent(
    """
    Implement a standalone Python 3 module (standard library only) containing two thread-safe or single-threaded per-key rate limiter classes: `TokenBucketLimiter` and `SlidingWindowLimiter`.

    ### Required API & Exact Behavior

    1. **`TokenBucketLimiter(capacity: float, refill_rate: float, now_fn: Callable[[], float] = time.monotonic)`**
       - Each unseen `key: str` starts full with `capacity` tokens.
       - Tokens refill lazily at `refill_rate` tokens per second based on `now_fn()`, capped at `capacity`.
       - `allow(key: str, cost: float = 1) -> bool`:
         - Raise `ValueError` if `cost <= 0`.
         - If `cost > capacity`, return `False` without modifying bucket state.
         - Refill up to `now_fn()`. If available tokens `>= cost` (allow `1e-12` float tolerance), deduct `cost` and return `True`; otherwise do not deduct tokens and return `False`.
       - `retry_after(key: str, cost: float = 1) -> float`:
         - Raise `ValueError` if `cost <= 0`.
         - If `cost > capacity`, return `float("inf")`.
         - After refilling to `now_fn()`, return `0.0` if available tokens `>= cost`, else `(cost - available) / refill_rate`.
       - `remaining(key: str) -> float`:
         - Return currently available tokens for `key` after refilling to `now_fn()` (without consuming any tokens).
       - `reset(key: str | None = None) -> None`:
         - If `key is None`, reset state for all keys. Otherwise reset only `key`.

    2. **`SlidingWindowLimiter(limit: int, window_seconds: float, now_fn: Callable[[], float] = time.monotonic)`**
       - Implements an exact sliding-window log (not fixed tumbling buckets).
       - An event recorded at time `t` expires once `now - t >= window_seconds` (so active events satisfy `now - t < window_seconds`).
       - `allow(key: str, cost: int = 1) -> bool`:
         - Raise `ValueError` if `cost <= 0`.
         - Prune expired timestamps for `key`. If `cost > limit` or `len(active) + cost > limit`, return `False` without recording anything.
         - Otherwise record `cost` timestamps at `now` and return `True`.
       - `retry_after(key: str, cost: int = 1) -> float`:
         - Raise `ValueError` if `cost <= 0`.
         - If `cost > limit`, return `float("inf")`.
         - Prune expired timestamps. If `len(active) + cost <= limit`, return `0.0`.
         - Otherwise compute the exact seconds until enough oldest active timestamps expire so `cost` units fit.
       - `remaining(key: str) -> int`:
         - Prune expired timestamps and return `limit - len(active)`.
       - `reset(key: str | None = None) -> None`:
         - Reset `key` or all keys if `key is None`.

    Return ONLY a single Python code block containing the complete implementation.
    """
).strip()

_TASK1_GOLDEN = textwrap.dedent(
    """
    from collections import deque
    import time
    from typing import Callable


    class TokenBucketLimiter:
        def __init__(
            self,
            capacity: float,
            refill_rate: float,
            now_fn: Callable[[], float] = time.monotonic,
        ) -> None:
            if capacity <= 0 or refill_rate <= 0:
                raise ValueError("capacity and refill_rate must be positive")
            self.capacity = float(capacity)
            self.refill_rate = float(refill_rate)
            self.now_fn = now_fn
            self._buckets: dict[str, tuple[float, float]] = {}

        def _refill(self, key: str, now: float) -> float:
            if key not in self._buckets:
                tokens = self.capacity
            else:
                prev_tokens, last_ts = self._buckets[key]
                elapsed = max(0.0, now - last_ts)
                tokens = min(self.capacity, prev_tokens + elapsed * self.refill_rate)
            self._buckets[key] = (tokens, now)
            return tokens

        def allow(self, key: str, cost: float = 1) -> bool:
            if cost <= 0:
                raise ValueError("cost must be positive")
            if cost > self.capacity:
                return False
            now = self.now_fn()
            tokens = self._refill(key, now)
            if tokens + 1e-12 >= cost:
                self._buckets[key] = (max(0.0, tokens - cost), now)
                return True
            return False

        def retry_after(self, key: str, cost: float = 1) -> float:
            if cost <= 0:
                raise ValueError("cost must be positive")
            if cost > self.capacity:
                return float("inf")
            now = self.now_fn()
            tokens = self._refill(key, now)
            if tokens + 1e-12 >= cost:
                return 0.0
            return (cost - tokens) / self.refill_rate

        def remaining(self, key: str) -> float:
            now = self.now_fn()
            return self._refill(key, now)

        def reset(self, key: str | None = None) -> None:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


    class SlidingWindowLimiter:
        def __init__(
            self,
            limit: int,
            window_seconds: float,
            now_fn: Callable[[], float] = time.monotonic,
        ) -> None:
            if limit <= 0 or window_seconds <= 0:
                raise ValueError("limit and window_seconds must be positive")
            self.limit = int(limit)
            self.window_seconds = float(window_seconds)
            self.now_fn = now_fn
            self._windows: dict[str, deque[float]] = {}

        def _prune(self, key: str, now: float) -> deque[float]:
            dq = self._windows.get(key)
            if dq is None:
                dq = deque()
                self._windows[key] = dq
                return dq
            cutoff = now - self.window_seconds
            while dq and dq[0] <= cutoff:
                dq.popleft()
            return dq

        def allow(self, key: str, cost: int = 1) -> bool:
            if cost <= 0:
                raise ValueError("cost must be positive")
            if cost > self.limit:
                return False
            now = self.now_fn()
            dq = self._prune(key, now)
            if len(dq) + cost > self.limit:
                return False
            for _ in range(cost):
                dq.append(now)
            return True

        def retry_after(self, key: str, cost: int = 1) -> float:
            if cost <= 0:
                raise ValueError("cost must be positive")
            if cost > self.limit:
                return float("inf")
            now = self.now_fn()
            dq = self._prune(key, now)
            needed = len(dq) + cost - self.limit
            if needed <= 0:
                return 0.0
            target_ts = dq[needed - 1]
            return max(0.0, (target_ts + self.window_seconds) - now)

        def remaining(self, key: str) -> int:
            now = self.now_fn()
            dq = self._prune(key, now)
            return self.limit - len(dq)

        def reset(self, key: str | None = None) -> None:
            if key is None:
                self._windows.clear()
            else:
                self._windows.pop(key, None)
    """
).strip()


# ---------------------------------------------------------------------------
# Task 2: Buggy Ledger Aggregator Debugging (Root-Cause Bug Fixing)
# ---------------------------------------------------------------------------

_TASK2_CHECKS = [
    "Decimal precision (no float drift)",
    "ROUND_HALF_UP half-cent rounding",
    "Timezone conversion to UTC",
    "Half-open window [start, end)",
    "Idempotency deduplication keeps earliest",
    "Deduplication before window filter",
    "Refund subtracted from net, excluded from gross",
    "Expected currencies zero-filled",
    "Sort net_revenue descending (numeric, not string)",
    "Empty input & tie-breaking alphabetical",
]

_TASK2_TEST_RUNNER = textwrap.dedent(
    """
    from datetime import datetime, timezone, timedelta
    import json
    import sys

    sys.path.insert(0, ".")
    checks = []

    def record(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "passed": True, "detail": detail or "ok"})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    import solution
    summarize_ledger = solution.summarize_ledger

    START = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    END = datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc)

    def by_cur(res):
        return {c["currency"]: c for c in res["currencies"]}

    def t1():
        recs = [
            {"idempotency_key": "k1", "timestamp": "2026-03-01T01:00:00Z", "currency": "USD", "type": "charge", "amount": "0.10"},
            {"idempotency_key": "k2", "timestamp": "2026-03-01T02:00:00Z", "currency": "USD", "type": "charge", "amount": "0.20"},
        ]
        res = summarize_ledger(recs, START, END)
        assert res["currencies"][0]["net_revenue"] == "0.30", res

    def t2():
        recs = [
            {"idempotency_key": "k1", "timestamp": "2026-03-01T01:00:00Z", "currency": "USD", "type": "charge", "amount": "1.005"},
            {"idempotency_key": "k2", "timestamp": "2026-03-01T01:05:00Z", "currency": "USD", "type": "charge", "amount": "2.015"},
        ]
        res = summarize_ledger(recs, START, END)
        assert res["currencies"][0]["gross_sales"] == "3.03", res

    def t3():
        # 2026-03-01T04:00:00+05:30 == 2026-02-28T22:30:00Z (outside)
        # 2026-03-01T22:00:00-03:00 == 2026-03-02T01:00:00Z (outside)
        # 2026-03-01T06:00:00+05:30 == 2026-03-01T00:30:00Z (inside)
        recs = [
            {"idempotency_key": "out1", "timestamp": "2026-03-01T04:00:00+05:30", "currency": "USD", "type": "charge", "amount": "10.00"},
            {"idempotency_key": "out2", "timestamp": "2026-03-01T22:00:00-03:00", "currency": "USD", "type": "charge", "amount": "20.00"},
            {"idempotency_key": "in1", "timestamp": "2026-03-01T06:00:00+05:30", "currency": "USD", "type": "charge", "amount": "5.00"},
        ]
        res = summarize_ledger(recs, START, END)
        assert res["total_processed"] == 1
        assert res["currencies"][0]["net_revenue"] == "5.00"

    def t4():
        # Naive start/end treated as UTC; exact start included, exact end excluded
        s_naive = datetime(2026, 3, 1, 0, 0, 0)
        e_naive = datetime(2026, 3, 2, 0, 0, 0)
        recs = [
            {"idempotency_key": "exact_start", "timestamp": "2026-03-01T00:00:00", "currency": "EUR", "type": "charge", "amount": "12.00"},
            {"idempotency_key": "exact_end", "timestamp": "2026-03-02T00:00:00Z", "currency": "EUR", "type": "charge", "amount": "99.00"},
        ]
        res = summarize_ledger(recs, s_naive, e_naive)
        assert res["total_processed"] == 1
        assert res["currencies"][0]["net_revenue"] == "12.00"

    def t5():
        recs = [
            {"idempotency_key": "dup", "timestamp": "2026-03-01T12:00:00Z", "currency": "USD", "type": "charge", "amount": "99.00"},
            {"idempotency_key": "dup", "timestamp": "2026-03-01T10:00:00Z", "currency": "USD", "type": "charge", "amount": "15.00"},
        ]
        res = summarize_ledger(recs, START, END)
        assert res["total_processed"] == 1
        assert res["currencies"][0]["net_revenue"] == "15.00"

    def t6():
        # Earliest occurrence is BEFORE start window; duplicate inside window must NOT be counted
        recs = [
            {"idempotency_key": "early", "timestamp": "2026-03-01T05:00:00Z", "currency": "USD", "type": "charge", "amount": "50.00"},
            {"idempotency_key": "early", "timestamp": "2026-02-28T23:59:00Z", "currency": "USD", "type": "charge", "amount": "50.00"},
        ]
        res = summarize_ledger(recs, START, END)
        assert res["total_processed"] == 0
        assert res["currencies"] == []

    def t7():
        recs = [
            {"idempotency_key": "c1", "timestamp": "2026-03-01T03:00:00Z", "currency": "GBP", "type": "charge", "amount": "100.00"},
            {"idempotency_key": "r1", "timestamp": "2026-03-01T04:00:00Z", "currency": "GBP", "type": "refund", "amount": "25.50"},
        ]
        res = summarize_ledger(recs, START, END)
        gbp = res["currencies"][0]
        assert gbp["gross_sales"] == "100.00", gbp
        assert gbp["refunds_total"] == "25.50", gbp
        assert gbp["net_revenue"] == "74.50", gbp
        assert gbp["charge_count"] == 1
        assert gbp["refund_count"] == 1

    def t8():
        recs = [
            {"idempotency_key": "c1", "timestamp": "2026-03-01T03:00:00Z", "currency": "USD", "type": "charge", "amount": "10.00"},
        ]
        res = summarize_ledger(recs, START, END, expected_currencies=["USD", "JPY", "EUR"])
        m = by_cur(res)
        assert set(m.keys()) == {"USD", "JPY", "EUR"}
        assert m["JPY"] == {
            "currency": "JPY",
            "gross_sales": "0.00",
            "refunds_total": "0.00",
            "net_revenue": "0.00",
            "charge_count": 0,
            "refund_count": 0,
        }, m["JPY"]

    def t9():
        recs = [
            {"idempotency_key": "c1", "timestamp": "2026-03-01T03:00:00Z", "currency": "USD", "type": "charge", "amount": "100.00"},
            {"idempotency_key": "c2", "timestamp": "2026-03-01T03:00:00Z", "currency": "EUR", "type": "charge", "amount": "90.00"},
            {"idempotency_key": "c3", "timestamp": "2026-03-01T03:00:00Z", "currency": "CAD", "type": "refund", "amount": "10.00"},
        ]
        res = summarize_ledger(recs, START, END)
        order = [c["currency"] for c in res["currencies"]]
        assert order == ["USD", "EUR", "CAD"], order

    def t10():
        res_empty = summarize_ledger([], START, END, expected_currencies=["ZAR", "AUD"])
        assert res_empty["total_processed"] == 0
        assert [c["currency"] for c in res_empty["currencies"]] == ["AUD", "ZAR"], res_empty

    tests = [
        ("Decimal precision (no float drift)", t1),
        ("ROUND_HALF_UP half-cent rounding", t2),
        ("Timezone conversion to UTC", t3),
        ("Half-open window [start, end)", t4),
        ("Idempotency deduplication keeps earliest", t5),
        ("Deduplication before window filter", t6),
        ("Refund subtracted from net, excluded from gross", t7),
        ("Expected currencies zero-filled", t8),
        ("Sort net_revenue descending (numeric, not string)", t9),
        ("Empty input & tie-breaking alphabetical", t10),
    ]
    for name, fn in tests:
        record(name, fn)

    print("__EVAL_RESULT__:" + json.dumps({"checks": checks}))
    """
)


def _grade_task2(response_text: str) -> dict[str, Any]:
    code = extract_code_block(response_text, ("python", "py"))
    checks, _ = _run_python_tests(code, _TASK2_TEST_RUNNER, _TASK2_CHECKS)
    return _build_grade_result(checks, code)


_TASK2_PROMPT = textwrap.dedent(
    """
    The Python module below has several bugs that cause financial reporting errors. Fix all bugs and return the complete corrected module.

    ### Specification for `summarize_ledger(records, start_utc, end_utc, expected_currencies=None)`
    1. **Timezone normalization**:
       - `start_utc` and `end_utc` may be timezone-aware or naive `datetime` objects (treat naive as UTC).
       - Each record's `"timestamp"` is an ISO-8601 string (e.g. `"Z"`, `"+05:30"`, `"-03:00"`, or naive which should be treated as UTC). Convert all timestamps to UTC before comparing.
    2. **Deduplication before windowing**:
       - Before filtering by the time window, deduplicate `records` by `"idempotency_key"`, keeping only the occurrence with the **earliest UTC timestamp** (if timestamps tie, keep the first occurrence in input order).
    3. **Half-open window filtering**:
       - Keep deduplicated records satisfying `start_utc <= tx_utc < end_utc`.
    4. **Exact decimal arithmetic & rounding**:
       - Parse `"amount"` with `Decimal(str(rec["amount"]))` (never binary floats).
       - Quantize each record's amount to `Decimal("0.01")` using `ROUND_HALF_UP` before accumulating.
       - Format all monetary totals as two-decimal strings (e.g. `"0.00"`, `"-10.00"`).
    5. **Transaction types**:
       - `"charge"`: adds to `gross_sales`, adds to `net_revenue`, increments `charge_count`.
       - `"refund"`: adds to `refunds_total`, **subtracts** from `net_revenue`, increments `refund_count`, and does **not** add to `gross_sales`.
    6. **Currencies & sorting**:
       - Include every currency present in the filtered records AND every currency in `expected_currencies` (if provided), initializing zero-activity currencies to `"0.00"` and `0` counts.
       - Sort `currencies` by numeric `net_revenue` **descending**, breaking ties by `currency` **alphabetically ascending**.
       - Return:
         ```python
         {
             "total_processed": int,  # number of filtered records
             "currencies": [
                 {
                     "currency": str,
                     "gross_sales": str,
                     "refunds_total": str,
                     "net_revenue": str,
                     "charge_count": int,
                     "refund_count": int,
                 },
                 ...
             ],
         }
         ```

    ### Buggy Code
    ```python
    from datetime import datetime


    def summarize_ledger(records, start_utc, end_utc, expected_currencies=None):
        start_s = start_utc.isoformat()
        end_s = end_utc.isoformat()

        filtered = []
        for rec in records:
            if start_s <= rec["timestamp"] <= end_s:
                filtered.append(rec)

        seen = set()
        deduped = []
        for rec in filtered:
            key = rec["idempotency_key"]
            if key not in seen:
                seen.add(key)
                deduped.append(rec)

        buckets = {}
        for rec in deduped:
            cur = rec["currency"]
            if cur not in buckets:
                buckets[cur] = {
                    "gross_sales": 0.0,
                    "refunds_total": 0.0,
                    "net_revenue": 0.0,
                    "charge_count": 0,
                    "refund_count": 0,
                }
            amt = round(float(rec["amount"]), 2)
            if rec["type"] == "charge":
                buckets[cur]["gross_sales"] += amt
                buckets[cur]["net_revenue"] += amt
                buckets[cur]["charge_count"] += 1
            elif rec["type"] == "refund":
                buckets[cur]["gross_sales"] += amt
                buckets[cur]["refunds_total"] += amt
                buckets[cur]["net_revenue"] += amt
                buckets[cur]["refund_count"] += 1

        summary = []
        for cur, data in buckets.items():
            summary.append({
                "currency": cur,
                "gross_sales": f"{data['gross_sales']:.2f}",
                "refunds_total": f"{data['refunds_total']:.2f}",
                "net_revenue": f"{data['net_revenue']:.2f}",
                "charge_count": data["charge_count"],
                "refund_count": data["refund_count"],
            })

        summary.sort(key=lambda item: item["net_revenue"], reverse=True)
        return {"total_processed": len(deduped), "currencies": summary}
    ```

    Return ONLY a single Python code block with the complete fixed module.
    """
).strip()

_TASK2_GOLDEN = textwrap.dedent(
    """
    from datetime import datetime, timezone
    from decimal import Decimal, ROUND_HALF_UP


    TWO_PLACES = Decimal("0.01")


    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


    def _parse_ts(ts: str) -> datetime:
        dt = datetime.fromisoformat(ts)
        return _to_utc(dt)


    def summarize_ledger(records, start_utc, end_utc, expected_currencies=None):
        start = _to_utc(start_utc)
        end = _to_utc(end_utc)

        earliest_by_key: dict[str, tuple[datetime, int, dict]] = {}
        for idx, rec in enumerate(records):
            key = rec["idempotency_key"]
            ts = _parse_ts(rec["timestamp"])
            prev = earliest_by_key.get(key)
            if prev is None or ts < prev[0]:
                earliest_by_key[key] = (ts, idx, rec)

        deduped = sorted(earliest_by_key.values(), key=lambda item: item[1])
        filtered = [rec for ts, _, rec in deduped if start <= ts < end]

        buckets: dict[str, dict] = {}
        for cur in (expected_currencies or []):
            buckets.setdefault(
                cur,
                {
                    "gross_sales": Decimal("0.00"),
                    "refunds_total": Decimal("0.00"),
                    "net_revenue": Decimal("0.00"),
                    "charge_count": 0,
                    "refund_count": 0,
                },
            )

        for rec in filtered:
            cur = rec["currency"]
            if cur not in buckets:
                buckets[cur] = {
                    "gross_sales": Decimal("0.00"),
                    "refunds_total": Decimal("0.00"),
                    "net_revenue": Decimal("0.00"),
                    "charge_count": 0,
                    "refund_count": 0,
                }
            amt = Decimal(str(rec["amount"])).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            if rec["type"] == "charge":
                buckets[cur]["gross_sales"] += amt
                buckets[cur]["net_revenue"] += amt
                buckets[cur]["charge_count"] += 1
            elif rec["type"] == "refund":
                buckets[cur]["refunds_total"] += amt
                buckets[cur]["net_revenue"] -= amt
                buckets[cur]["refund_count"] += 1

        summary = []
        for cur, data in buckets.items():
            summary.append(
                {
                    "currency": cur,
                    "gross_sales": f"{data['gross_sales']:.2f}",
                    "refunds_total": f"{data['refunds_total']:.2f}",
                    "net_revenue": f"{data['net_revenue']:.2f}",
                    "charge_count": data["charge_count"],
                    "refund_count": data["refund_count"],
                }
            )

        summary.sort(key=lambda item: (-Decimal(item["net_revenue"]), item["currency"]))
        return {"total_processed": len(filtered), "currencies": summary}
    """
).strip()


# ---------------------------------------------------------------------------
# Task 3: Structured Incident Extraction (Information Synthesis & PII Guard)
# ---------------------------------------------------------------------------

def _grade_task3(response_text: str) -> dict[str, Any]:
    data, raw, parse_err = extract_json_payload(response_text)
    required_keys = {
        "incident_id",
        "severity",
        "started_at_utc",
        "resolved_at_utc",
        "duration_minutes",
        "introducing_version",
        "root_cause",
        "affected_services",
        "affected_customers",
        "action_items",
    }

    checks: list[dict[str, Any]] = []
    valid_obj = isinstance(data, dict) and required_keys.issubset(data.keys())
    checks.append(
        {
            "name": "Valid JSON & required schema keys",
            "passed": valid_obj,
            "detail": (
                "ok"
                if valid_obj
                else (parse_err or f"Missing keys: {sorted(required_keys - set(data.keys())) if isinstance(data, dict) else type(data).__name__}")
            ),
        }
    )

    if not isinstance(data, dict):
        for name in [
            "Exact incident_id & severity",
            "UTC started_at_utc",
            "UTC resolved_at_utc",
            "Exact duration_minutes (87)",
            "Introducing version v2.14.3",
            "True root cause (pool exhaustion, no Redis)",
            "Exact affected_services set",
            "Revised affected_customers (418)",
            "Three action items & zero PII leakage",
        ]:
            checks.append({"name": name, "passed": False, "detail": "Invalid JSON object"})
        return _build_grade_result(checks, raw)

    id_sev_ok = data.get("incident_id") == "INC-4821" and data.get("severity") == "SEV-2"
    checks.append(
        {
            "name": "Exact incident_id & severity",
            "passed": id_sev_ok,
            "detail": f"incident_id={data.get('incident_id')!r}, severity={data.get('severity')!r}",
        }
    )

    start_ok = data.get("started_at_utc") == "2026-10-14T08:12:00Z"
    checks.append(
        {
            "name": "UTC started_at_utc",
            "passed": start_ok,
            "detail": f"got {data.get('started_at_utc')!r}",
        }
    )

    end_ok = data.get("resolved_at_utc") == "2026-10-14T09:39:00Z"
    checks.append(
        {
            "name": "UTC resolved_at_utc",
            "passed": end_ok,
            "detail": f"got {data.get('resolved_at_utc')!r}",
        }
    )

    dur_ok = data.get("duration_minutes") == 87
    checks.append(
        {
            "name": "Exact duration_minutes (87)",
            "passed": dur_ok,
            "detail": f"got {data.get('duration_minutes')!r}",
        }
    )

    ver_ok = data.get("introducing_version") == "v2.14.3"
    checks.append(
        {
            "name": "Introducing version v2.14.3",
            "passed": ver_ok,
            "detail": f"got {data.get('introducing_version')!r}",
        }
    )

    rc = str(data.get("root_cause", ""))
    rc_l = rc.lower()
    rc_ok = (
        ("pool" in rc_l or "connection" in rc_l)
        and ("transaction" in rc_l or "unclosed" in rc_l or "leak" in rc_l)
        and ("redis" not in rc_l)
    )
    checks.append(
        {
            "name": "True root cause (pool exhaustion, no Redis)",
            "passed": rc_ok,
            "detail": rc[:140],
        }
    )

    svc_raw = data.get("affected_services")
    expected_svcs = {"checkout-api", "order-worker", "webhook-dispatcher"}
    svc_ok = isinstance(svc_raw, list) and set(svc_raw) == expected_svcs and len(svc_raw) == 3
    checks.append(
        {
            "name": "Exact affected_services set",
            "passed": svc_ok,
            "detail": f"got {svc_raw!r}",
        }
    )

    cust_ok = data.get("affected_customers") == 418
    checks.append(
        {
            "name": "Revised affected_customers (418)",
            "passed": cust_ok,
            "detail": f"got {data.get('affected_customers')!r}",
        }
    )

    items = data.get("action_items")
    expected_items = {
        ("priya", "2026-10-18"),
        ("alex", "2026-10-21"),
        ("dana", "2026-10-25"),
    }
    items_ok = False
    if isinstance(items, list) and len(items) == 3:
        got_pairs = {
            (str(it.get("owner", "")).strip().lower(), str(it.get("due_date", "")).strip())
            for it in items
            if isinstance(it, dict) and str(it.get("task", "")).strip()
        }
        items_ok = got_pairs == expected_items
    serialized_lower = json.dumps(data).lower()
    pii_tokens = ["marcus.chen", "northstar-retail", "555-0194", "marcus chen"]
    no_pii = not any(tok in serialized_lower for tok in pii_tokens)
    checks.append(
        {
            "name": "Three action items & zero PII leakage",
            "passed": items_ok and no_pii,
            "detail": f"items_ok={items_ok}, no_pii={no_pii}",
        }
    )

    return _build_grade_result(checks, json.dumps(data, indent=2))


_TASK3_PROMPT = textwrap.dedent(
    """
    Extract a canonical post-incident report as strict JSON from the noisy multi-channel incident log below.

    ### Output JSON Schema
    Return ONLY a single JSON object matching these exact keys and types:
    ```json
    {
      "incident_id": "string (e.g. INC-4821)",
      "severity": "string (final confirmed severity, e.g. SEV-2)",
      "started_at_utc": "ISO-8601 UTC string ending in Z, YYYY-MM-DDTHH:MM:SSZ",
      "resolved_at_utc": "ISO-8601 UTC string ending in Z, YYYY-MM-DDTHH:MM:SSZ",
      "duration_minutes": 0,
      "introducing_version": "string (e.g. v2.14.3)",
      "root_cause": "concise 1-sentence summary of the confirmed root cause only (do NOT mention disproven hypotheses)",
      "affected_services": ["sorted list of affected service names"],
      "affected_customers": 0,
      "action_items": [
        {"owner": "lowercase first name", "due_date": "YYYY-MM-DD", "task": "concise description"}
      ]
    }
    ```

    ### Strict Rules
    - Convert all timestamps accurately to UTC (`Z`). Use the confirmed actual start and resolution times, not the initial alert time.
    - Do NOT include any customer PII (customer names, emails, or phone numbers) anywhere in the output.
    - Reflect final corrected facts (severity, root cause, affected customer count, affected services).

    ### Incident Log
    [PagerDuty Alert] 2026-10-14T08:15:00Z | Incident INC-4821 triggered | Initial triage severity: SEV-1 | Alert: 5xx rate > 15% on checkout-api.

    [Slack #inc-4821]
    - 2026-10-14T01:17:00-07:00 @bob: Paging on-call. Looks like Redis cache OOM on cache-west-2? Seeing timeouts in checkout-api. Rough dashboard says maybe 1,200 customers impacted.
    - 2026-10-14T04:22:00-04:00 @dana (IC): I'm Incident Commander. Please check promo-code endpoint and search-service too. Forwarded customer escalation: "Marcus Chen (marcus.chen.vip@northstar-retail.example, +1-415-555-0194) cannot complete checkout."
    - 2026-10-14T08:31:00Z @priya: Wait — Redis cache-west-2 memory is at 42% with 0 evictions. Not Redis! Looking at postgres-primary instead: max connections exhausted (`remaining connection slots are reserved`).
    - 2026-10-14T08:40:00Z @priya: Confirmed! Deploy `v2.14.3` of `checkout-api` (which shipped at 2026-10-14T01:10:00-07:00) forgot to close a PostgreSQL database transaction in the promo-code validator, causing connection pool exhaustion on `postgres-primary`.
    - 2026-10-14T08:44:00Z @alex: Checking telemetry: customer-facing 5xx errors actually started 3 minutes before PagerDuty fired, right at `2026-10-14T01:12:00-07:00`. Also confirmed `search-service` is completely healthy and unaffected; the three impacted services are `checkout-api`, `order-worker`, and `webhook-dispatcher`.
    - 2026-10-14T08:52:00Z @dana (IC): Downgrading confirmed severity from SEV-1 to `SEV-2` since core storefront browsing works. Rolling back `checkout-api` to `v2.14.2`.

    [Email Follow-up — 2026-10-14T11:10:00Z]
    From: Dana Reyes
    Subject: INC-4821 Wrap-up
    Rollback completed and all services fully recovered at `2026-10-14T05:39:00-04:00`.
    Data warehouse deduplication shows the initial 1,200 estimate was inflated by retries; final count of affected customers is **418**.
    Confirmed follow-up action items:
    1. Priya will add a context-managed transaction wrapper and connection-leak linter check in `checkout-api` by 2026-10-18.
    2. Alex will configure PgBouncer pool saturation alerts at an 80% threshold by 2026-10-21.
    3. Dana will add an automated connection-leak canary gate to the pre-prod deploy pipeline by 2026-10-25.
    """
).strip()

_TASK3_GOLDEN = json.dumps(
    {
        "incident_id": "INC-4821",
        "severity": "SEV-2",
        "started_at_utc": "2026-10-14T08:12:00Z",
        "resolved_at_utc": "2026-10-14T09:39:00Z",
        "duration_minutes": 87,
        "introducing_version": "v2.14.3",
        "root_cause": "Deploy v2.14.3 of checkout-api introduced an unclosed PostgreSQL transaction in the promo-code validator, causing connection pool exhaustion on postgres-primary.",
        "affected_services": ["checkout-api", "order-worker", "webhook-dispatcher"],
        "affected_customers": 418,
        "action_items": [
            {
                "owner": "priya",
                "due_date": "2026-10-18",
                "task": "Add context-managed transaction wrapper and connection-leak linter check in checkout-api",
            },
            {
                "owner": "alex",
                "due_date": "2026-10-21",
                "task": "Configure PgBouncer pool saturation alerts at an 80% threshold",
            },
            {
                "owner": "dana",
                "due_date": "2026-10-25",
                "task": "Add an automated connection-leak canary gate to the pre-prod deploy pipeline",
            },
        ],
    },
    indent=2,
)


# ---------------------------------------------------------------------------
# Task 4: SaaS Analytics SQL Query (Relational Fan-out & Edge Cases)
# ---------------------------------------------------------------------------

_TASK4_SCHEMA_AND_SEED = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL
);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    billing_month TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE usage_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    event_month TEXT NOT NULL,
    api_calls INTEGER NOT NULL
);

INSERT INTO accounts (id, name, segment) VALUES
    (1, 'Acme Corp', 'enterprise'),
    (2, 'Beta Labs', 'growth'),
    (3, 'Gamma Co', 'enterprise'),
    (4, 'Delta App', 'starter');

INSERT INTO invoices (id, account_id, billing_month, amount_cents, status) VALUES
    -- 2026-01:
    -- Account 1 (enterprise): 2 paid invoices (10000 + 5000 = 15000), usage = 60+50 = 110 >= 100 -> active
    -- Account 2 (growth): 1 paid invoice (5000), usage = 120 >= 100 -> active
    -- Account 3 (enterprise): 1 paid invoice (5000), usage = 80 < 100 -> NOT active, still counts in paid_mrr_cents
    -- Account 4 (starter): 1 void invoice (2000), usage = 200 -> NOT active, counts in void_or_refunded_cents
    (1, 1, '2026-01', 10000, 'paid'),
    (2, 1, '2026-01', 5000, 'paid'),
    (3, 2, '2026-01', 5000, 'paid'),
    (4, 3, '2026-01', 5000, 'paid'),
    (5, 4, '2026-01', 2000, 'void'),
    -- 2026-02:
    -- Account 1 (enterprise): 1 paid (12000), 1 refunded (3000), usage = 50+60 = 110 -> active
    -- Account 2 (growth): 1 paid (3000), no usage events -> NOT active
    -- Account 4 (starter): 1 paid (2000), usage = 150 -> active
    (6, 1, '2026-02', 12000, 'paid'),
    (7, 1, '2026-02', 3000, 'refunded'),
    (8, 2, '2026-02', 3000, 'paid'),
    (9, 4, '2026-02', 2000, 'paid'),
    -- 2026-03:
    -- Only void/refunded invoices -> active_accounts=0, paid_mrr_cents=0, void_or_refunded_cents=4500, enterprise_mrr_share_pct=0.0
    (10, 1, '2026-03', 3000, 'void'),
    (11, 2, '2026-03', 1500, 'refunded');

INSERT INTO usage_events (id, account_id, event_month, api_calls) VALUES
    (1, 1, '2026-01', 60),
    (2, 1, '2026-01', 50),
    (3, 2, '2026-01', 120),
    (4, 3, '2026-01', 80),
    (5, 4, '2026-01', 200),
    (6, 1, '2026-02', 50),
    (7, 1, '2026-02', 60),
    (8, 4, '2026-02', 150),
    (9, 1, '2026-03', 500);
"""


def _grade_task4(response_text: str) -> dict[str, Any]:
    sql = extract_code_block(response_text, ("sql", "sqlite"))
    sql_clean = sql.strip().rstrip(";").strip()
    checks: list[dict[str, Any]] = []

    expected_cols = [
        "month",
        "active_accounts",
        "paid_mrr_cents",
        "void_or_refunded_cents",
        "enterprise_mrr_share_pct",
    ]
    expected_rows = {
        "2026-01": {"active_accounts": 2, "paid_mrr_cents": 25000, "void_or_refunded_cents": 2000, "pct": 80.0},
        "2026-02": {"active_accounts": 2, "paid_mrr_cents": 17000, "void_or_refunded_cents": 3000, "pct": 70.6},
        "2026-03": {"active_accounts": 0, "paid_mrr_cents": 0, "void_or_refunded_cents": 4500, "pct": 0.0},
    }

    def fail_rest(reason: str):
        while len(checks) < 10:
            names = [
                "Valid single SQLite SELECT query",
                "Exact 5 output column names",
                "Returns 3 monthly rows in ascending order",
                "2026-01 active_accounts (2)",
                "2026-01 paid_mrr_cents without fan-out (25000)",
                "2026-01 enterprise_mrr_share_pct (80.0)",
                "2026-02 active_accounts (2) & paid_mrr_cents (17000)",
                "2026-02 void_or_refunded_cents (3000) & enterprise share (70.6)",
                "2026-03 zero-paid month active & MRR (0, 0)",
                "2026-03 void_or_refunded (4500) & zero-div pct (0.0)",
            ]
            checks.append({"name": names[len(checks)], "passed": False, "detail": reason})

    if not sql_clean:
        fail_rest("Empty SQL output")
        return _build_grade_result(checks, sql)

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_TASK4_SCHEMA_AND_SEED)
        steps = 0

        def progress_cb():
            nonlocal steps
            steps += 1
            return 1 if steps > 200_000 else 0

        conn.set_progress_handler(progress_cb, 1000)
        cur = conn.execute(sql_clean)
        rows = cur.fetchall()
        col_names = [d[0] for d in (cur.description or [])]
    except Exception as exc:
        conn.close()
        fail_rest(f"{type(exc).__name__}: {exc}")
        return _build_grade_result(checks, sql)
    conn.close()

    checks.append({"name": "Valid single SQLite SELECT query", "passed": True, "detail": "ok"})
    checks.append(
        {
            "name": "Exact 5 output column names",
            "passed": col_names == expected_cols,
            "detail": f"got {col_names}",
        }
    )

    if len(col_names) != 5 or len(rows) != 3:
        checks.append(
            {
                "name": "Returns 3 monthly rows in ascending order",
                "passed": False,
                "detail": f"rows={len(rows)}, cols={col_names}",
            }
        )
        fail_rest("Row/column mismatch")
        return _build_grade_result(checks, sql)

    months = [r[0] for r in rows]
    checks.append(
        {
            "name": "Returns 3 monthly rows in ascending order",
            "passed": months == ["2026-01", "2026-02", "2026-03"],
            "detail": f"months={months}",
        }
    )

    row_map = {r[0]: r for r in rows}
    r1 = row_map.get("2026-01")
    r2 = row_map.get("2026-02")
    r3 = row_map.get("2026-03")

    checks.append(
        {
            "name": "2026-01 active_accounts (2)",
            "passed": bool(r1 and r1[1] == expected_rows["2026-01"]["active_accounts"]),
            "detail": f"got {r1[1] if r1 else None}",
        }
    )
    checks.append(
        {
            "name": "2026-01 paid_mrr_cents without fan-out (25000)",
            "passed": bool(r1 and r1[2] == expected_rows["2026-01"]["paid_mrr_cents"]),
            "detail": f"got {r1[2] if r1 else None}",
        }
    )
    checks.append(
        {
            "name": "2026-01 enterprise_mrr_share_pct (80.0)",
            "passed": bool(r1 and r1[4] is not None and abs(float(r1[4]) - 80.0) < 0.05),
            "detail": f"got {r1[4] if r1 else None}",
        }
    )
    checks.append(
        {
            "name": "2026-02 active_accounts (2) & paid_mrr_cents (17000)",
            "passed": bool(r2 and r2[1] == 2 and r2[2] == 17000),
            "detail": f"got active={r2[1] if r2 else None}, paid={r2[2] if r2 else None}",
        }
    )
    checks.append(
        {
            "name": "2026-02 void_or_refunded_cents (3000) & enterprise share (70.6)",
            "passed": bool(r2 and r2[3] == 3000 and r2[4] is not None and abs(float(r2[4]) - 70.6) < 0.05),
            "detail": f"got void_ref={r2[3] if r2 else None}, pct={r2[4] if r2 else None}",
        }
    )
    checks.append(
        {
            "name": "2026-03 zero-paid month active & MRR (0, 0)",
            "passed": bool(r3 and r3[1] == 0 and r3[2] == 0),
            "detail": f"got active={r3[1] if r3 else None}, paid={r3[2] if r3 else None}",
        }
    )
    checks.append(
        {
            "name": "2026-03 void_or_refunded (4500) & zero-div pct (0.0)",
            "passed": bool(r3 and r3[3] == 4500 and r3[4] is not None and abs(float(r3[4]) - 0.0) < 0.05),
            "detail": f"got void_ref={r3[3] if r3 else None}, pct={r3[4] if r3 else None}",
        }
    )

    return _build_grade_result(checks, sql)


_TASK4_PROMPT = textwrap.dedent(
    """
    Given the SQLite schema below, write a **single** SQLite `SELECT` query (CTEs are allowed, no DDL/DML) returning one row per `billing_month` present in `invoices`, ordered by `month ASC`.

    ### Schema
    ```sql
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        segment TEXT NOT NULL -- 'enterprise', 'growth', or 'starter'
    );

    CREATE TABLE invoices (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        billing_month TEXT NOT NULL, -- 'YYYY-MM'
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL -- 'paid', 'void', or 'refunded'
    );

    CREATE TABLE usage_events (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        event_month TEXT NOT NULL, -- 'YYYY-MM'
        api_calls INTEGER NOT NULL
    );
    ```

    ### Required Output Columns (exact names & order)
    1. `month`: `billing_month` (`'YYYY-MM'`).
    2. `active_accounts`: count of distinct `account_id`s that BOTH have at least one `'paid'` invoice in that month AND have `SUM(api_calls) >= 100` in `usage_events` for that `(account_id, event_month = billing_month)`.
    3. `paid_mrr_cents`: total `amount_cents` across all `'paid'` invoices in that month (`0` if none).
    4. `void_or_refunded_cents`: total `amount_cents` across all `'void'` or `'refunded'` invoices in that month (`0` if none).
    5. `enterprise_mrr_share_pct`: percentage (`0.0` to `100.0`, rounded to **1 decimal place**) of that month's `paid_mrr_cents` coming from accounts with `segment = 'enterprise'`. Must return `0.0` if `paid_mrr_cents = 0`.

    ### Important Pitfalls to Avoid
    - Both `invoices` and `usage_events` can contain **multiple rows** for the same `(account_id, month)` — do not create a many-to-many join fan-out that inflates `paid_mrr_cents` or `void_or_refunded_cents`.
    - Be careful with SQLite integer division (`integer / integer` truncates in SQLite unless cast to `REAL` or multiplied by `100.0`).

    Return ONLY a single SQL code block containing the query.
    """
).strip()

_TASK4_GOLDEN = textwrap.dedent(
    """
    WITH monthly_usage AS (
        SELECT
            account_id,
            event_month AS month,
            SUM(api_calls) AS total_calls
        FROM usage_events
        GROUP BY account_id, event_month
    ),
    monthly_account_invoices AS (
        SELECT
            i.billing_month AS month,
            i.account_id,
            a.segment,
            SUM(CASE WHEN i.status = 'paid' THEN i.amount_cents ELSE 0 END) AS paid_cents,
            SUM(CASE WHEN i.status IN ('void', 'refunded') THEN i.amount_cents ELSE 0 END) AS void_ref_cents,
            MAX(CASE WHEN i.status = 'paid' THEN 1 ELSE 0 END) AS has_paid
        FROM invoices i
        JOIN accounts a ON a.id = i.account_id
        GROUP BY i.billing_month, i.account_id, a.segment
    )
    SELECT
        mai.month AS month,
        COALESCE(
            SUM(CASE WHEN mai.has_paid = 1 AND COALESCE(mu.total_calls, 0) >= 100 THEN 1 ELSE 0 END),
            0
        ) AS active_accounts,
        COALESCE(SUM(mai.paid_cents), 0) AS paid_mrr_cents,
        COALESCE(SUM(mai.void_ref_cents), 0) AS void_or_refunded_cents,
        CASE
            WHEN COALESCE(SUM(mai.paid_cents), 0) = 0 THEN 0.0
            ELSE ROUND(
                SUM(CASE WHEN mai.segment = 'enterprise' THEN mai.paid_cents ELSE 0 END) * 100.0
                / SUM(mai.paid_cents),
                1
            )
        END AS enterprise_mrr_share_pct
    FROM monthly_account_invoices mai
    LEFT JOIN monthly_usage mu
        ON mu.account_id = mai.account_id
        AND mu.month = mai.month
    GROUP BY mai.month
    ORDER BY mai.month ASC;
    """
).strip()


# ---------------------------------------------------------------------------
# Task 5: Release Dependency & Resource Scheduling (Constraint Optimization)
# ---------------------------------------------------------------------------

def _grade_task5(response_text: str) -> dict[str, Any]:
    data, raw, parse_err = extract_json_payload(response_text)
    services = {"auth", "billing", "catalog", "gateway", "notifications", "search"}
    engineers = {"alice", "bob", "carol"}
    skills = {
        "auth": {"alice", "bob"},
        "billing": {"alice", "bob"},
        "catalog": {"alice", "bob", "carol"},
        "gateway": {"alice", "carol"},
        "notifications": {"bob", "carol"},
        "search": {"carol"},
    }
    available = {
        "alice": {0, 1, 3, 4, 5, 6, 7},
        "bob": {1, 2, 3, 4, 5, 6, 7},
        "carol": {0, 1, 2, 3, 4, 5, 6, 7},
    }
    deps = [
        ("auth", "billing"),
        ("auth", "gateway"),
        ("catalog", "gateway"),
        ("catalog", "search"),
        ("billing", "notifications"),
        ("gateway", "notifications"),
    ]
    frozen = {("gateway", 1), ("billing", 2)}

    checks: list[dict[str, Any]] = []
    valid_schema = isinstance(data, dict) and isinstance(data.get("schedule"), list)
    checks.append(
        {
            "name": "Valid JSON & schedule array",
            "passed": valid_schema,
            "detail": "ok" if valid_schema else (parse_err or "Missing 'schedule' array"),
        }
    )

    if not valid_schema:
        for name in [
            "All 6 services present uniquely",
            "Valid slot integers (0..7)",
            "Valid engineer names",
            "All dependency edges strictly ordered",
            "Engineer skill constraints satisfied",
            "Engineer availability windows satisfied",
            "No engineer double-booked in same slot",
            "Freeze windows & max 2 concurrent per slot",
            "Optimal makespan achieved (max slot == 3)",
        ]:
            checks.append({"name": name, "passed": False, "detail": "Invalid JSON schedule"})
        return _build_grade_result(checks, raw)

    entries = data["schedule"]
    by_svc: dict[str, dict[str, Any]] = {}
    dup_or_bad = False
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("service"), str):
            dup_or_bad = True
            continue
        svc = entry["service"].strip()
        if svc in by_svc:
            dup_or_bad = True
        by_svc[svc] = entry

    all_six = not dup_or_bad and set(by_svc.keys()) == services and len(entries) == 6
    checks.append(
        {
            "name": "All 6 services present uniquely",
            "passed": all_six,
            "detail": f"services={sorted(by_svc.keys())}, count={len(entries)}",
        }
    )

    slots_ok = bool(by_svc) and all(
        isinstance(e.get("slot"), int) and not isinstance(e.get("slot"), bool) and 0 <= e["slot"] <= 7
        for e in by_svc.values()
    )
    checks.append(
        {
            "name": "Valid slot integers (0..7)",
            "passed": slots_ok,
            "detail": str({k: v.get("slot") for k, v in by_svc.items()}),
        }
    )

    eng_ok = bool(by_svc) and all(e.get("engineer") in engineers for e in by_svc.values())
    checks.append(
        {
            "name": "Valid engineer names",
            "passed": eng_ok,
            "detail": str({k: v.get("engineer") for k, v in by_svc.items()}),
        }
    )

    dep_violations = []
    if all_six and slots_ok:
        for before, after in deps:
            if not (by_svc[before]["slot"] < by_svc[after]["slot"]):
                dep_violations.append(f"{before}({by_svc[before]['slot']})<{after}({by_svc[after]['slot']})")
    checks.append(
        {
            "name": "All dependency edges strictly ordered",
            "passed": all_six and slots_ok and not dep_violations,
            "detail": "ok" if not dep_violations else ", ".join(dep_violations),
        }
    )

    skill_violations = []
    for svc, e in by_svc.items():
        if svc in skills and e.get("engineer") not in skills[svc]:
            skill_violations.append(f"{svc}:{e.get('engineer')}")
    checks.append(
        {
            "name": "Engineer skill constraints satisfied",
            "passed": all_six and not skill_violations,
            "detail": "ok" if not skill_violations else ", ".join(skill_violations),
        }
    )

    avail_violations = []
    for svc, e in by_svc.items():
        eng = e.get("engineer")
        slot = e.get("slot")
        if eng in available and isinstance(slot, int) and slot not in available[eng]:
            avail_violations.append(f"{eng}@{slot}")
    checks.append(
        {
            "name": "Engineer availability windows satisfied",
            "passed": all_six and slots_ok and eng_ok and not avail_violations,
            "detail": "ok" if not avail_violations else ", ".join(avail_violations),
        }
    )

    double_booked = []
    seen_eng_slot: set[tuple[str, int]] = set()
    for svc, e in by_svc.items():
        pair = (e.get("engineer"), e.get("slot"))
        if pair in seen_eng_slot:
            double_booked.append(f"{pair}")
        seen_eng_slot.add(pair)
    checks.append(
        {
            "name": "No engineer double-booked in same slot",
            "passed": all_six and slots_ok and eng_ok and not double_booked,
            "detail": "ok" if not double_booked else ", ".join(double_booked),
        }
    )

    slot_counts: dict[int, int] = {}
    freeze_hits = []
    for svc, e in by_svc.items():
        slot = e.get("slot")
        if isinstance(slot, int):
            slot_counts[slot] = slot_counts.get(slot, 0) + 1
            if (svc, slot) in frozen:
                freeze_hits.append(f"{svc}@{slot}")
    over_cap = [f"slot {s}:{c}" for s, c in slot_counts.items() if c > 2]
    checks.append(
        {
            "name": "Freeze windows & max 2 concurrent per slot",
            "passed": all_six and slots_ok and not freeze_hits and not over_cap,
            "detail": "ok" if not (freeze_hits or over_cap) else f"freeze={freeze_hits}, cap={over_cap}",
        }
    )

    all_hard_ok = all(c["passed"] for c in checks)
    max_slot = max((e["slot"] for e in by_svc.values()), default=99) if slots_ok else 99
    checks.append(
        {
            "name": "Optimal makespan achieved (max slot == 3)",
            "passed": all_hard_ok and max_slot == 3,
            "detail": f"max_slot={max_slot}, hard_ok={all_hard_ok}",
        }
    )

    return _build_grade_result(checks, json.dumps(data, indent=2))


_TASK5_PROMPT = textwrap.dedent(
    """
    Create an optimal deployment schedule for 6 microservices across 1-hour time slots (`0` through `7`) using 3 engineers (`alice`, `bob`, `carol`). Each deployment takes exactly 1 slot.

    ### Constraints
    1. **Dependencies** (`A -> B` means `slot(A) < slot(B)`):
       - `auth -> billing`
       - `auth -> gateway`
       - `catalog -> gateway`
       - `catalog -> search`
       - `billing -> notifications`
       - `gateway -> notifications`
    2. **Engineer Skills** (only listed engineers may deploy that service):
       - `auth`: `alice`, `bob`
       - `billing`: `alice`, `bob`
       - `catalog`: `alice`, `bob`, `carol`
       - `gateway`: `alice`, `carol`
       - `notifications`: `bob`, `carol`
       - `search`: `carol`
    3. **Engineer Availability**:
       - `alice` is **unavailable** during slot `2` (available in `0, 1, 3, 4, 5, 6, 7`).
       - `bob` is **unavailable** during slot `0` (available in `1, 2, 3, 4, 5, 6, 7`).
       - `carol` is available in all slots `0..7`.
    4. **Freeze Windows**:
       - `gateway` cannot be deployed in slot `1`.
       - `billing` cannot be deployed in slot `2`.
    5. **Capacity Limits**:
       - Each engineer can run **at most 1** deployment per slot.
       - At most **2 deployments total** may run concurrently in any single slot.
    6. **Objective**:
       - Minimize the final completion slot (`max(slot)` across all 6 services).

    ### Output Format
    Return ONLY a single JSON object matching this exact schema:
    ```json
    {
      "schedule": [
        {"service": "auth", "slot": 0, "engineer": "alice"}
      ]
    }
    ```
    """
).strip()

_TASK5_GOLDEN = json.dumps(
    {
        "schedule": [
            {"service": "auth", "slot": 0, "engineer": "alice"},
            {"service": "catalog", "slot": 0, "engineer": "carol"},
            {"service": "billing", "slot": 1, "engineer": "bob"},
            {"service": "search", "slot": 1, "engineer": "carol"},
            {"service": "gateway", "slot": 2, "engineer": "carol"},
            {"service": "notifications", "slot": 3, "engineer": "bob"},
        ]
    },
    indent=2,
)


# ---------------------------------------------------------------------------
# Task 6: Reverse-Proxy Log Parser (Spec Implementation)
# ---------------------------------------------------------------------------

_TASK6_CHECKS = [
    "Standard IPv4 line without metadata",
    "Bracketed IPv6 host stripped cleanly",
    "Escaped quotes and backslashes inside path",
    "Dash '-' parses to None for bytes & upstream_ms",
    "Meta values with bare tokens and quoted spaces",
    "Meta quoted value containing '=' and escaped quotes",
    "Whitespace trimming and blank/comment lines -> None",
    "Invalid status code (out of 100..599 or non-int) -> None",
    "Unclosed quote in path or meta -> None",
    "Negative bytes/latency or malformed meta key -> None",
]

_TASK6_TEST_RUNNER = textwrap.dedent(
    r"""
    import json
    import sys

    sys.path.insert(0, ".")
    checks = []

    def record(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "passed": True, "detail": detail or "ok"})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    import solution
    parse_proxy_log = solution.parse_proxy_log

    def t1():
        line = '2026-10-14T08:15:00Z 192.168.1.42 GET "/api/v1/orders?page=2" 200 1482 14.5'
        got = parse_proxy_log(line)
        assert got == {
            "timestamp": "2026-10-14T08:15:00Z",
            "client_ip": "192.168.1.42",
            "method": "GET",
            "path": "/api/v1/orders?page=2",
            "status": 200,
            "bytes": 1482,
            "upstream_ms": 14.5,
            "meta": {},
        }, got
        assert type(got["status"]) is int
        assert type(got["bytes"]) is int
        assert type(got["upstream_ms"]) is float

    def t2():
        line = '2026-10-14T08:15:01+02:00 [2001:db8::1] POST "/webhooks" 202 0 3'
        got = parse_proxy_log(line)
        assert got["client_ip"] == "2001:db8::1", got
        assert got["upstream_ms"] == 3.0 and type(got["upstream_ms"]) is float, got

    def t3():
        line = r'2026-10-14T08:15:02Z [::1] GET "/search?q=\"hello\"&dir=C:\\tmp" 200 512 8.25'
        got = parse_proxy_log(line)
        assert got is not None, "Returned None"
        assert got["path"] == r'/search?q="hello"&dir=C:\tmp', got

    def t4():
        line = '2026-10-14T08:15:03Z 10.0.0.5 HEAD "/healthz" 204 - -'
        got = parse_proxy_log(line)
        assert got["bytes"] is None and got["upstream_ms"] is None, got

    def t5():
        line = '2026-10-14T08:15:04Z 10.0.0.5 DELETE "/items/9" 500 128 120.4 region=us-east ua="Mozilla/5.0 (Mac)" retry=2'
        got = parse_proxy_log(line)
        assert got["meta"] == {"region": "us-east", "ua": "Mozilla/5.0 (Mac)", "retry": "2"}, got

    def t6():
        line = r'2026-10-14T08:15:05Z 10.0.0.5 PATCH "/items/9" 200 64 5.0 note="a=b \"ok\" \\done" cache=HIT'
        got = parse_proxy_log(line)
        assert got is not None, "Returned None"
        assert got["meta"] == {"note": r'a=b "ok" \done', "cache": "HIT"}, got

    def t7():
        assert parse_proxy_log("") is None
        assert parse_proxy_log("   \n") is None
        assert parse_proxy_log("# comment line") is None
        line = '  2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 10 1.0  \n'
        assert parse_proxy_log(line)["status"] == 200

    def t8():
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 99 10 1.0') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 600 10 1.0') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" abc 10 1.0') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 get "/ok" 200 10 1.0') is None

    def t9():
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/unclosed 200 10 1.0') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 10 1.0 ua="unclosed') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 10') is None

    def t10():
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 -5 1.0') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 10 -1.0') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 10 1.0 badtoken') is None
        assert parse_proxy_log('2026-10-14T08:15:00Z 1.2.3.4 GET "/ok" 200 10 1.0 =val') is None

    tests = [
        ("Standard IPv4 line without metadata", t1),
        ("Bracketed IPv6 host stripped cleanly", t2),
        ("Escaped quotes and backslashes inside path", t3),
        ("Dash '-' parses to None for bytes & upstream_ms", t4),
        ("Meta values with bare tokens and quoted spaces", t5),
        ("Meta quoted value containing '=' and escaped quotes", t6),
        ("Whitespace trimming and blank/comment lines -> None", t7),
        ("Invalid status code (out of 100..599 or non-int) -> None", t8),
        ("Unclosed quote in path or meta -> None", t9),
        ("Negative bytes/latency or malformed meta key -> None", t10),
    ]
    for name, fn in tests:
        record(name, fn)

    print("__EVAL_RESULT__:" + json.dumps({"checks": checks}))
    """
)


def _grade_task6(response_text: str) -> dict[str, Any]:
    code = extract_code_block(response_text, ("python", "py"))
    checks, _ = _run_python_tests(code, _TASK6_TEST_RUNNER, _TASK6_CHECKS)
    return _build_grade_result(checks, code)


_TASK6_PROMPT = textwrap.dedent(
    """
    Implement a Python 3 function `parse_proxy_log(line: str) -> dict | None` (standard library only) that strictly parses a custom reverse-proxy access log format.

    ### Log Format
    ```text
    <iso_ts> <client_ip> <method> "<path>" <status> <bytes> <upstream_ms> [key=value ...]
    ```

    ### Specification
    1. Strip leading/trailing whitespace. Return `None` for empty lines or comment lines starting with `#`.
    2. `timestamp`: first non-whitespace token (e.g. `2026-10-14T08:15:00Z` or `2026-10-14T08:15:01+02:00`; must contain `T`).
    3. `client_ip`: non-whitespace token. If wrapped in `[...]` (e.g. `[2001:db8::1]` or `[::1]`), strip the surrounding brackets.
    4. `method`: uppercase ASCII HTTP method (`[A-Z]+`, e.g. `GET`, `POST`). Lowercase or invalid methods must return `None`.
    5. `path`: double-quoted string `"..."`. Inside quotes, `\\"` unescapes to `"` and `\\\\` unescapes to `\\`. Unquoted or unclosed paths must return `None`.
    6. `status`: integer in `100..599` inclusive (`int`). Otherwise return `None`.
    7. `bytes`: either `-` (parsed as `None`) or a non-negative integer (`int`). Negative or non-integer values return `None`.
    8. `upstream_ms`: either `-` (parsed as `None`) or a non-negative number parsed as `float` (e.g. `3` -> `3.0`). Negative or invalid numbers (`NaN`/`inf`) return `None`.
    9. Optional trailing `key=value` pairs separated by whitespace:
       - `key` must match `^[A-Za-z0-9_.-]+$`.
       - `value` is either a bare non-whitespace token OR a double-quoted string `"..."` that may contain spaces, `=`, `\\"`, and `\\\\` (unescape `\\"` -> `"` and `\\\\` -> `\\`).
       - Return `None` for missing `=`, empty key, unclosed quotes, or malformed trailing tokens.
    10. Return dictionary with exact keys:
        ```python
        {
            "timestamp": str,
            "client_ip": str,
            "method": str,
            "path": str,
            "status": int,
            "bytes": int | None,
            "upstream_ms": float | None,
            "meta": dict[str, str],
        }
        ```

    Return ONLY a single Python code block containing `parse_proxy_log` and any helpers.
    """
).strip()

_TASK6_GOLDEN = textwrap.dedent(
    """
    import math
    import re


    _KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
    _METHOD_RE = re.compile(r"^[A-Z]+$")


    def _skip_ws(s: str, i: int) -> int:
        n = len(s)
        while i < n and s[i].isspace():
            i += 1
        return i


    def _read_quoted(s: str, i: int) -> tuple[str, int] | None:
        n = len(s)
        if i >= n or s[i] != '"':
            return None
        i += 1
        out: list[str] = []
        while i < n:
            ch = s[i]
            if ch == "\\\\":
                if i + 1 >= n:
                    return None
                nxt = s[i + 1]
                if nxt in ('"', "\\\\"):
                    out.append(nxt)
                else:
                    out.append("\\\\" + nxt)
                i += 2
            elif ch == '"':
                return "".join(out), i + 1
            else:
                out.append(ch)
                i += 1
        return None


    def _read_token(s: str, i: int) -> tuple[str, int] | None:
        n = len(s)
        if i >= n:
            return None
        start = i
        while i < n and not s[i].isspace():
            i += 1
        if i == start:
            return None
        return s[start:i], i


    def parse_proxy_log(line: str) -> dict | None:
        if not isinstance(line, str):
            return None
        s = line.strip()
        if not s or s.startswith("#"):
            return None

        i = 0
        tok = _read_token(s, i)
        if not tok:
            return None
        ts, i = tok
        if "T" not in ts:
            return None

        i = _skip_ws(s, i)
        tok = _read_token(s, i)
        if not tok:
            return None
        ip_raw, i = tok
        if ip_raw.startswith("[") and ip_raw.endswith("]") and len(ip_raw) > 2:
            client_ip = ip_raw[1:-1]
        elif "[" in ip_raw or "]" in ip_raw:
            return None
        else:
            client_ip = ip_raw

        i = _skip_ws(s, i)
        tok = _read_token(s, i)
        if not tok:
            return None
        method, i = tok
        if not _METHOD_RE.match(method):
            return None

        i = _skip_ws(s, i)
        q = _read_quoted(s, i)
        if not q:
            return None
        path, i = q
        if i < len(s) and not s[i].isspace():
            return None

        i = _skip_ws(s, i)
        tok = _read_token(s, i)
        if not tok:
            return None
        status_raw, i = tok
        if not status_raw.isdigit():
            return None
        status = int(status_raw)
        if not (100 <= status <= 599):
            return None

        i = _skip_ws(s, i)
        tok = _read_token(s, i)
        if not tok:
            return None
        bytes_raw, i = tok
        if bytes_raw == "-":
            byte_count = None
        elif bytes_raw.isdigit():
            byte_count = int(bytes_raw)
        else:
            return None

        i = _skip_ws(s, i)
        tok = _read_token(s, i)
        if not tok:
            return None
        ms_raw, i = tok
        if ms_raw == "-":
            upstream_ms = None
        else:
            try:
                upstream_ms = float(ms_raw)
            except ValueError:
                return None
            if not math.isfinite(upstream_ms) or upstream_ms < 0.0:
                return None

        meta: dict[str, str] = {}
        n = len(s)
        while i < n:
            i = _skip_ws(s, i)
            if i >= n:
                break
            eq = s.find("=", i)
            if eq == -1:
                return None
            key = s[i:eq]
            if not _KEY_RE.match(key):
                return None
            val_start = eq + 1
            if val_start >= n or s[val_start].isspace():
                return None
            if s[val_start] == '"':
                qv = _read_quoted(s, val_start)
                if not qv:
                    return None
                val, i = qv
                if i < n and not s[i].isspace():
                    return None
            else:
                tv = _read_token(s, val_start)
                if not tv:
                    return None
                val, i = tv
                if '"' in val:
                    return None
            meta[key] = val

        return {
            "timestamp": ts,
            "client_ip": client_ip,
            "method": method,
            "path": path,
            "status": status,
            "bytes": byte_count,
            "upstream_ms": upstream_ms,
            "meta": meta,
        }
    """
).strip()


TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        id="code-rate-limiter",
        title="Token Bucket & Sliding Window Rate Limiters",
        short_label="Code",
        category="Algorithms & State",
        description="Implement dual stateful rate limiters with atomic multi-cost deduction, exact window expiry, retry-after calculation, and reset semantics.",
        prompt=_TASK1_PROMPT,
        grader=_grade_task1,
        golden_solution=_TASK1_GOLDEN,
    ),
    BenchmarkTask(
        id="debug-csv-aggregator",
        title="Multi-Currency Financial Ledger Bug Fix",
        short_label="Debug",
        category="Root-Cause Debugging",
        description="Fix 5 production bugs in a payment aggregator: float drift, timezone windowing, idempotency deduplication order, refund math, and numeric sorting.",
        prompt=_TASK2_PROMPT,
        grader=_grade_task2,
        golden_solution=_TASK2_GOLDEN,
    ),
    BenchmarkTask(
        id="extract-incident-report",
        title="Noisy Incident Thread to Canonical JSON",
        short_label="Extract",
        category="Structured Extraction",
        description="Synthesize conflicting Slack, PagerDuty, and email updates into strict UTC-normalized JSON while filtering disproven causes and customer PII.",
        prompt=_TASK3_PROMPT,
        grader=_grade_task3,
        golden_solution=_TASK3_GOLDEN,
    ),
    BenchmarkTask(
        id="sql-saas-analytics",
        title="SaaS MRR & Active Cohort SQLite Query",
        short_label="SQL",
        category="Relational Analytics",
        description="Write a fan-out-safe SQLite query combining invoices, usage events, and enterprise revenue share with zero-division handling.",
        prompt=_TASK4_PROMPT,
        grader=_grade_task4,
        golden_solution=_TASK4_GOLDEN,
    ),
    BenchmarkTask(
        id="logic-release-schedule",
        title="Microservice Rollout Constraint Solver",
        short_label="Planning",
        category="Constraint Satisfaction",
        description="Schedule 6 services across 3 engineers subject to DAG dependencies, skills, availability, freeze windows, and concurrency limits.",
        prompt=_TASK5_PROMPT,
        grader=_grade_task5,
        golden_solution=_TASK5_GOLDEN,
    ),
    BenchmarkTask(
        id="spec-log-parser",
        title="Reverse-Proxy Access Log Parser",
        short_label="Parser",
        category="Spec Implementation",
        description="Parse bracketed IPv6, escaped quotes in paths, nullable metrics, and quoted key=value metadata while rejecting malformed lines.",
        prompt=_TASK6_PROMPT,
        grader=_grade_task6,
        golden_solution=_TASK6_GOLDEN,
    ),
]

TASK_BY_ID: dict[str, BenchmarkTask] = {t.id: t for t in TASKS}


def run_self_test() -> int:
    """Verify every golden solution scores 100.0 and broken input scores < 50.0."""
    all_ok = True
    for task in TASKS:
        good = task.grade(task.golden_solution)
        bad = task.grade("sorry, I cannot complete this task.")
        status = "PASS" if (good["score"] == 100.0 and bad["score"] < 50.0) else "FAIL"
        print(
            f"[{status}] {task.id:<25} golden={good['score']:5.1f}% ({good['passed_checks']}/{good['total_checks']}) "
            f"broken={bad['score']:5.1f}%"
        )
        if status != "PASS":
            all_ok = False
            for chk in good["checks"]:
                if not chk["passed"]:
                    print(f"   FAILED CHECK: {chk['name']} -> {chk['detail']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test())
    print(f"Benchmark Suite v{SUITE_VERSION}: {len(TASKS)} tasks")
    for task in TASKS:
        print(f" - {task.id}: {task.title} [{task.category}]")
