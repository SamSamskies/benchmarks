"""Real-world benchmark task suite (v2.0.0) with deterministic automated graders."""

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


SUITE_VERSION = "2.0.0"

COMMON_SYSTEM_PROMPT = (
    "You are being evaluated on a real-world engineering task. "
    "Follow all specifications, schemas, complexity limits, edge cases, and output format requirements precisely."
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
                timeout=12,
            )
        except subprocess.TimeoutExpired:
            return [
                {"name": name, "passed": False, "detail": "Execution timed out (12s)"}
                for name in check_names
            ], "Timed out after 12s"

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
# Task 1: Stateful Code Tracing Puzzles (Exact Execution & Edge Cases)
# ---------------------------------------------------------------------------

_TASK1_EXPECTED = {
    "q1": 30,
    "q2": 3310,
    "q3": 23,
    "q4": 32,
    "q5": "3:1:float:false",
    "q6": 8,
    "q7": 16,
    "q8": 369,
    "q9": 1215,
    "q10": 1805,
}


def _grade_task1(response_text: str) -> dict[str, Any]:
    data, raw, parse_err = extract_json_payload(response_text)
    checks: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        for q in [f"q{i}" for i in range(1, 11)]:
            checks.append(
                {
                    "name": f"Puzzle {q}",
                    "passed": False,
                    "detail": parse_err or "Invalid JSON object",
                }
            )
        return _build_grade_result(checks, raw)

    for q, expected in _TASK1_EXPECTED.items():
        got = data.get(q)
        passed = (type(got) is type(expected)) and (got == expected)
        checks.append(
            {
                "name": f"Puzzle {q}",
                "passed": passed,
                "detail": f"expected={expected!r}, got={got!r}",
            }
        )
    return _build_grade_result(checks, json.dumps(data, indent=2))


_TASK1_PROMPT = textwrap.dedent(
    """
    Mentally trace all 10 standalone Python 3 snippets below (`q1` through `q10`) and compute the exact value printed by each snippet.

    Return ONLY a single JSON object with keys `"q1"` through `"q10"` matching the exact printed value and type (`int` for integer outputs, `str` for string outputs).

    ### Snippets
    ```python
    # q1
    a = [[0, 1]] * 3
    a[0][1] += 5
    fns = [lambda x: x + i for i in range(3)]
    i = 4
    print(sum(r[1] for r in a) + sum(fn(2) for fn in fns))

    # q2
    def g(x):
        try:
            if x == 1:
                raise ValueError("boom")
            return 10
        except ValueError:
            return 20
        finally:
            if x < 2:
                return 30
    print(g(0) * 100 + g(1) * 10 + g(2))

    # q3
    from itertools import groupby
    rows = [("a", 2), ("a", 3), ("b", 5), ("a", 7), ("b", 1)]
    groups = [(k, list(gr)) for k, gr in groupby(rows, key=lambda r: r[0])]
    print(sum(len(items) * sum(v for _, v in items) for _, items in groups))

    # q4
    import re
    s = "ababa_aba"
    m1 = re.findall(r"(?=(aba))", s)
    m2 = re.findall(r"a.*?a", s)
    print(len(m1) * 10 + len(m2))

    # q5 (JSON string)
    d = {1: "int", True: "bool", 1.0: "float", 2: "two"}
    d[0] = "zero"
    d[False] = "false"
    print(f"{len(d)}:{repr(list(d.keys())[0])}:{d[True]}:{d[0]}")

    # q6
    dp = [1, 1, 2]
    for k in range(3, 10):
        dp.append((dp[-1] + 2 * dp[-2] - dp[-3]) % 100)
    print(dp[9])

    # q7
    state = 0b10110
    for step in range(6):
        if state & 1:
            state = ((state ^ 0b1101) >> 1) | 0b10000
        else:
            state = (state >> 1) ^ (step + 3)
        state &= 0b11111
    print(state)

    # q8
    edges = {0: [(1, 4), (2, 1)], 1: [(3, 2)], 2: [(1, 2), (3, 6), (4, 9)], 3: [(4, 3)], 4: []}
    dist = {n: 999 for n in range(5)}
    dist[0] = 0
    for u in range(5):
        for v, w in edges[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
    print(dist[1] * 100 + dist[3] * 10 + dist[4])

    # q9
    from decimal import Decimal, ROUND_HALF_UP
    vals = ["0.5", "1.5", "2.5", "3.5", "4.5"]
    ra = sum(round(float(v)) for v in vals)
    rb = sum(int(Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) for v in vals)
    print(ra * 100 + rb)

    # q10
    ivs = [(10, 15), (2, 6), (18, 22), (4, 8), (21, 25)]
    ivs.sort()
    merged = []
    for st, en in ivs:
        if not merged or st > merged[-1][1]:
            merged.append([st, en])
        else:
            merged[-1][1] = max(merged[-1][1], en)
    gaps = sum(merged[idx + 1][0] - merged[idx][1] for idx in range(len(merged) - 1))
    covered = sum(en - st for st, en in merged)
    print(covered * 100 + gaps)
    ```

    ### Output Schema
    ```json
    {
      "q1": 0,
      "q2": 0,
      "q3": 0,
      "q4": 0,
      "q5": "string",
      "q6": 0,
      "q7": 0,
      "q8": 0,
      "q9": 0,
      "q10": 0
    }
    ```
    """
).strip()

_TASK1_GOLDEN = json.dumps(_TASK1_EXPECTED, indent=2)


# ---------------------------------------------------------------------------
# Task 2: Bounded TTL + LRU Cache Debugging (Without Hinting the Bugs)
# ---------------------------------------------------------------------------

_TASK2_CHECKS = [
    "get() promotes key in LRU order",
    "Overwriting existing key updates used_bytes accurately",
    "Oversized set() returns False without evicting live items",
    "Overwrite with larger size triggers LRU eviction cleanly",
    "invalidate_prefix() reclaims bytes and ignores expired keys",
    "set() purges expired items before evicting live items",
    "stats() excludes expired items and tracks exact bytes",
    "delete() returns False on expired/missing and reclaims bytes",
    "get_or_compute() caches falsy values and skips caching exceptions",
    "Argument validation and zero-byte value support",
]

_TASK2_TEST_RUNNER = textwrap.dedent(
    """
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
    BoundedTTLCache = solution.BoundedTTLCache

    class FakeClock:
        def __init__(self, t=0.0):
            self.t = t
        def __call__(self):
            return self.t
        def advance(self, dt):
            self.t += dt

    def t1():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        assert c.set("a", 1, 60, 40) is True
        assert c.set("b", 2, 60, 40) is True
        assert c.get("a") == 1
        assert c.set("c", 3, 60, 40) is True
        assert c.get("a") == 1, "a was recently used and must stay"
        assert c.get("b") is None, "b was LRU and should be evicted"
        assert c.get("c") == 3

    def t2():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        assert c.set("a", "v1", 60, 30) is True
        assert c.set("a", "v2", 60, 50) is True
        assert c.stats() == {"entries": 1, "used_bytes": 50}, c.stats()
        assert c.set("a", "v3", 60, 10) is True
        assert c.stats() == {"entries": 1, "used_bytes": 10}, c.stats()

    def t3():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        assert c.set("a", 1, 60, 40) is True
        assert c.set("b", 2, 60, 40) is True
        assert c.set("huge", 3, 60, 101) is False
        assert c.get("a") == 1 and c.get("b") == 2
        assert c.stats() == {"entries": 2, "used_bytes": 80}

    def t4():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        assert c.set("a", 1, 60, 40) is True
        assert c.set("b", 2, 60, 40) is True
        assert c.set("a", 99, 60, 70) is True
        assert c.get("a") == 99
        assert c.get("b") is None
        assert c.stats() == {"entries": 1, "used_bytes": 70}, c.stats()

    def t5():
        clk = FakeClock()
        c = BoundedTTLCache(200, now_fn=clk)
        c.set("u:1", 1, 10, 30)
        c.set("u:2", 2, 50, 40)
        c.set("u:3", 3, 50, 20)
        c.set("o:1", 4, 50, 15)
        clk.advance(15)  # u:1 is now expired
        removed = c.invalidate_prefix("u:")
        assert removed == 2, f"Expected 2 live removed, got {removed}"
        assert c.stats() == {"entries": 1, "used_bytes": 15}, c.stats()
        assert c.get("o:1") == 4

    def t6():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        c.set("exp", 1, 5, 60)
        c.set("live", 2, 60, 40)
        clk.advance(6)  # exp is dead; live has 40 bytes
        assert c.set("new", 3, 60, 50) is True
        assert c.get("live") == 2, "Expired item should have been purged before evicting live"
        assert c.stats() == {"entries": 2, "used_bytes": 90}

    def t7():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        c.set("a", 1, 10, 25)
        c.set("b", 2, 20, 35)
        clk.advance(10)  # exact boundary now >= expires_at -> a expired
        assert c.stats() == {"entries": 1, "used_bytes": 35}, c.stats()
        assert c.get("a") is None

    def t8():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        c.set("a", 1, 5, 30)
        clk.advance(6)
        assert c.delete("a") is False
        assert c.stats() == {"entries": 0, "used_bytes": 0}
        c.set("b", 2, 20, 40)
        assert c.delete("b") is True
        assert c.delete("b") is False
        assert c.stats() == {"entries": 0, "used_bytes": 0}

    def t9():
        clk = FakeClock()
        c = BoundedTTLCache(100, now_fn=clk)
        calls = 0
        for falsy in (0, "", False, None):
            k = f"k_{repr(falsy)}"
            def make_loader(val):
                def _load():
                    nonlocal calls
                    calls += 1
                    return val
                return _load
            assert c.get_or_compute(k, make_loader(falsy), 30, 10) is falsy
            assert c.get_or_compute(k, make_loader(falsy), 30, 10) is falsy
        assert calls == 4, f"Loader called {calls} times instead of 4"
        def boom():
            raise RuntimeError("db down")
        try:
            c.get_or_compute("err", boom, 30, 10)
            raise AssertionError("Expected RuntimeError")
        except RuntimeError:
            pass
        assert c.get("err", "MISSING") == "MISSING"

    def t10():
        for bad in (0, -5):
            try:
                BoundedTTLCache(bad)
                raise AssertionError("Expected ValueError for max_bytes")
            except ValueError:
                pass
        c = BoundedTTLCache(10)
        for args in [("k", 1, 0, 1), ("k", 1, -1, 1), ("k", 1, 10, -1)]:
            try:
                c.set(*args)
                raise AssertionError(f"Expected ValueError for {args}")
            except ValueError:
                pass
        assert c.set("z", "empty", 10, 0) is True
        assert c.get("z") == "empty"
        assert c.stats() == {"entries": 1, "used_bytes": 0}

    tests = [
        ("get() promotes key in LRU order", t1),
        ("Overwriting existing key updates used_bytes accurately", t2),
        ("Oversized set() returns False without evicting live items", t3),
        ("Overwrite with larger size triggers LRU eviction cleanly", t4),
        ("invalidate_prefix() reclaims bytes and ignores expired keys", t5),
        ("set() purges expired items before evicting live items", t6),
        ("stats() excludes expired items and tracks exact bytes", t7),
        ("delete() returns False on expired/missing and reclaims bytes", t8),
        ("get_or_compute() caches falsy values and skips caching exceptions", t9),
        ("Argument validation and zero-byte value support", t10),
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
    The Python module below implements `BoundedTTLCache`, a byte-bounded LRU+TTL in-memory cache, but it contains several subtle bugs in production. Identify and fix **all** bugs without changing the public method signatures, and return the complete corrected module.

    ### Behavioral Contract
    - `BoundedTTLCache(max_bytes: int, now_fn=time.monotonic)`:
      - Raise `ValueError` if `max_bytes <= 0`.
    - `set(key: str, value: Any, ttl: float, size_bytes: int) -> bool`:
      - Raise `ValueError` if `ttl <= 0` or `size_bytes < 0`.
      - If `size_bytes > max_bytes`, leave existing live entries untouched and return `False`.
      - Expired entries (`now >= expires_at`) must not trigger eviction of live entries and must never count toward `used_bytes`.
      - Updating an existing key must update its value, TTL, recency, and `used_bytes` accurately (evicting LRU entries if needed when the new size is larger).
    - `get(key: str, default: Any = None) -> Any`:
      - Returns stored value (including falsy values such as `0`, `""`, `False`, `None`) and marks `key` most-recently-used; returns `default` if absent or expired.
    - `delete(key: str) -> bool`:
      - Removes `key` and frees its bytes; returns `True` only if `key` was present and unexpired.
    - `invalidate_prefix(prefix: str) -> int`:
      - Removes all entries starting with `prefix`, frees their bytes, and returns the count of **live** entries removed.
    - `get_or_compute(key: str, loader: Callable[[], Any], ttl: float, size_bytes: int) -> Any`:
      - Returns cached live value (even if falsy/None) without invoking `loader`; otherwise invokes `loader()`, caches on success, and lets exceptions propagate without caching.
    - `stats() -> dict[str, int]`:
      - Returns `{"entries": <live count>, "used_bytes": <live bytes>}`.

    ### Buggy Code
    ```python
    from collections import OrderedDict
    import time
    from typing import Any, Callable


    class BoundedTTLCache:
        def __init__(self, max_bytes: int, now_fn: Callable[[], float] = time.monotonic) -> None:
            if max_bytes <= 0:
                raise ValueError("max_bytes must be positive")
            self._max_bytes = max_bytes
            self._now_fn = now_fn
            self._items: OrderedDict[str, tuple[Any, float, int]] = OrderedDict()
            self._used_bytes = 0

        def set(self, key: str, value: Any, ttl: float, size_bytes: int) -> bool:
            if ttl <= 0 or size_bytes < 0:
                raise ValueError("ttl must be > 0 and size_bytes >= 0")

            now = self._now_fn()
            expires_at = now + ttl

            while self._items and self._used_bytes + size_bytes > self._max_bytes:
                _, (_, _, evicted_size) = self._items.popitem(last=False)
                self._used_bytes -= evicted_size

            if size_bytes > self._max_bytes:
                return False

            self._items[key] = (value, expires_at, size_bytes)
            self._items.move_to_end(key)
            self._used_bytes += size_bytes
            return True

        def get(self, key: str, default: Any = None) -> Any:
            entry = self._items.get(key)
            if entry is None:
                return default
            value, expires_at, size_bytes = entry
            if self._now_fn() >= expires_at:
                del self._items[key]
                self._used_bytes -= size_bytes
                return default
            return value

        def delete(self, key: str) -> bool:
            entry = self._items.pop(key, None)
            if entry is None:
                return False
            _, _, size_bytes = entry
            self._used_bytes -= size_bytes
            return True

        def invalidate_prefix(self, prefix: str) -> int:
            targets = [k for k in self._items if k.startswith(prefix)]
            for k in targets:
                del self._items[k]
            return len(targets)

        def get_or_compute(
            self, key: str, loader: Callable[[], Any], ttl: float, size_bytes: int
        ) -> Any:
            cached = self.get(key)
            if cached:
                return cached
            value = loader()
            self.set(key, value, ttl, size_bytes)
            return value

        def stats(self) -> dict[str, int]:
            return {"entries": len(self._items), "used_bytes": self._used_bytes}
    ```

    Return ONLY a single Python code block containing the complete fixed module.
    """
).strip()

_TASK2_GOLDEN = textwrap.dedent(
    """
    from collections import OrderedDict
    import time
    from typing import Any, Callable


    _MISSING = object()


    class BoundedTTLCache:
        def __init__(self, max_bytes: int, now_fn: Callable[[], float] = time.monotonic) -> None:
            if max_bytes <= 0:
                raise ValueError("max_bytes must be positive")
            self._max_bytes = max_bytes
            self._now_fn = now_fn
            self._items: OrderedDict[str, tuple[Any, float, int]] = OrderedDict()
            self._used_bytes = 0

        def _purge_expired(self, now: float) -> None:
            expired = [k for k, (_, exp, _) in self._items.items() if now >= exp]
            for k in expired:
                _, _, sz = self._items.pop(k)
                self._used_bytes -= sz

        def set(self, key: str, value: Any, ttl: float, size_bytes: int) -> bool:
            if ttl <= 0 or size_bytes < 0:
                raise ValueError("ttl must be > 0 and size_bytes >= 0")
            if size_bytes > self._max_bytes:
                return False

            now = self._now_fn()
            self._purge_expired(now)

            if key in self._items:
                _, _, old_size = self._items.pop(key)
                self._used_bytes -= old_size

            while self._items and self._used_bytes + size_bytes > self._max_bytes:
                _, (_, _, evicted_size) = self._items.popitem(last=False)
                self._used_bytes -= evicted_size

            self._items[key] = (value, now + ttl, size_bytes)
            self._items.move_to_end(key)
            self._used_bytes += size_bytes
            return True

        def get(self, key: str, default: Any = None) -> Any:
            entry = self._items.get(key)
            if entry is None:
                return default
            value, expires_at, size_bytes = entry
            if self._now_fn() >= expires_at:
                del self._items[key]
                self._used_bytes -= size_bytes
                return default
            self._items.move_to_end(key)
            return value

        def delete(self, key: str) -> bool:
            now = self._now_fn()
            self._purge_expired(now)
            entry = self._items.pop(key, None)
            if entry is None:
                return False
            _, _, size_bytes = entry
            self._used_bytes -= size_bytes
            return True

        def invalidate_prefix(self, prefix: str) -> int:
            now = self._now_fn()
            self._purge_expired(now)
            targets = [k for k in self._items if k.startswith(prefix)]
            for k in targets:
                _, _, sz = self._items.pop(k)
                self._used_bytes -= sz
            return len(targets)

        def get_or_compute(
            self, key: str, loader: Callable[[], Any], ttl: float, size_bytes: int
        ) -> Any:
            cached = self.get(key, _MISSING)
            if cached is not _MISSING:
                return cached
            value = loader()
            self.set(key, value, ttl, size_bytes)
            return value

        def stats(self) -> dict[str, int]:
            self._purge_expired(self._now_fn())
            return {"entries": len(self._items), "used_bytes": self._used_bytes}
    """
).strip()


# ---------------------------------------------------------------------------
# Task 3: Gaps-and-Islands Sessionization & Attribution SQL
# ---------------------------------------------------------------------------

_TASK3_SCHEMA_AND_SEED = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    signup_date TEXT NOT NULL,
    country TEXT NOT NULL
);
CREATE TABLE page_views (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    viewed_at_sec INTEGER NOT NULL,
    page TEXT NOT NULL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    ordered_at_sec INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL
);

INSERT INTO users (id, signup_date, country) VALUES
    (1, '2026-01-01', 'US'),
    (2, '2026-01-03', 'US'),
    (3, '2026-01-05', 'DE'),
    (4, '2026-01-07', 'CA');

INSERT INTO page_views (id, user_id, viewed_at_sec, page) VALUES
    -- User 1 (US): Session 1 [100..700] dur=600; Session 2 [5000..6200] dur=1200
    (1, 1, 100, '/home'),
    (2, 1, 700, '/pricing'),
    (3, 1, 5000, '/docs'),
    (4, 1, 5300, '/checkout'),
    (5, 1, 6200, '/receipt'),
    -- User 2 (US): Session 3 [10000..10000] dur=0
    (6, 2, 10000, '/home'),
    -- User 3 (DE): exact 1800 gap [1000..2800] dur=1800, then +1801 [4601..4601] dur=0
    (7, 3, 1000, '/home'),
    (8, 3, 2800, '/cart'),
    (9, 3, 4601, '/home');
    -- User 4 (CA): 0 page_views

INSERT INTO orders (id, user_id, ordered_at_sec, amount_cents, status) VALUES
    -- User 1: two completed orders inside Session 1 attribution window [100..2500], one outside at 3000
    (1, 1, 650, 3000, 'completed'),
    (2, 1, 900, 2000, 'completed'),
    (3, 1, 3000, 7000, 'completed'),
    -- User 3: completed right at 2800 + 1800 = 4600 (< 4601 so uniquely Session 1); cancelled ignored
    (4, 3, 4600, 5000, 'completed'),
    (5, 3, 4601, 9999, 'cancelled'),
    -- User 4 (CA): completed order with no sessions -> unattributed
    (6, 4, 5000, 4000, 'completed');
"""


def _grade_task3(response_text: str) -> dict[str, Any]:
    sql = extract_code_block(response_text, ("sql", "sqlite"))
    sql_clean = sql.strip().rstrip(";").strip()
    checks: list[dict[str, Any]] = []

    expected_cols = [
        "country",
        "total_sessions",
        "avg_session_duration_sec",
        "converted_sessions",
        "attributed_revenue_cents",
        "session_conversion_rate_pct",
    ]
    check_names = [
        "Valid single SQLite SELECT query",
        "Exact 6 output column names",
        "Returns 3 country rows sorted ASC (CA, DE, US)",
        "CA preserves zero-session country with 0/0.0",
        "DE total_sessions == 2 (exact 1800s boundary)",
        "DE avg_session_duration_sec == 900.0",
        "DE converted_sessions=1, revenue=5000, rate=50.0",
        "US total_sessions=3 & avg_session_duration_sec=600.0",
        "US converted_sessions=1 & revenue=5000 (no fan-out)",
        "US session_conversion_rate_pct == 33.3",
    ]

    def fail_rest(reason: str):
        while len(checks) < 10:
            checks.append({"name": check_names[len(checks)], "passed": False, "detail": reason})

    if not sql_clean:
        fail_rest("Empty SQL output")
        return _build_grade_result(checks, sql)

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_TASK3_SCHEMA_AND_SEED)
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

    checks.append({"name": check_names[0], "passed": True, "detail": "ok"})
    checks.append({"name": check_names[1], "passed": col_names == expected_cols, "detail": f"got {col_names}"})

    if len(col_names) != 6 or len(rows) != 3:
        checks.append({"name": check_names[2], "passed": False, "detail": f"rows={len(rows)}, cols={col_names}"})
        fail_rest("Row/column mismatch")
        return _build_grade_result(checks, sql)

    countries = [r[0] for r in rows]
    checks.append({"name": check_names[2], "passed": countries == ["CA", "DE", "US"], "detail": f"got {countries}"})

    by_c = {r[0]: r for r in rows}
    ca, de, us = by_c.get("CA"), by_c.get("DE"), by_c.get("US")

    checks.append(
        {
            "name": check_names[3],
            "passed": bool(ca and ca[1] == 0 and abs(float(ca[2]) - 0.0) < 0.05 and ca[3] == 0 and ca[4] == 0 and abs(float(ca[5]) - 0.0) < 0.05),
            "detail": f"CA={ca}",
        }
    )
    checks.append(
        {
            "name": check_names[4],
            "passed": bool(de and de[1] == 2),
            "detail": f"got {de[1] if de else None}",
        }
    )
    checks.append(
        {
            "name": check_names[5],
            "passed": bool(de and de[2] is not None and abs(float(de[2]) - 900.0) < 0.05),
            "detail": f"got {de[2] if de else None}",
        }
    )
    checks.append(
        {
            "name": check_names[6],
            "passed": bool(de and de[3] == 1 and de[4] == 5000 and de[5] is not None and abs(float(de[5]) - 50.0) < 0.05),
            "detail": f"DE={de}",
        }
    )
    checks.append(
        {
            "name": check_names[7],
            "passed": bool(us and us[1] == 3 and us[2] is not None and abs(float(us[2]) - 600.0) < 0.05),
            "detail": f"got sessions={us[1] if us else None}, avg={us[2] if us else None}",
        }
    )
    checks.append(
        {
            "name": check_names[8],
            "passed": bool(us and us[3] == 1 and us[4] == 5000),
            "detail": f"got conv={us[3] if us else None}, rev={us[4] if us else None}",
        }
    )
    checks.append(
        {
            "name": check_names[9],
            "passed": bool(us and us[5] is not None and abs(float(us[5]) - 33.3) < 0.05),
            "detail": f"got {us[5] if us else None}",
        }
    )

    return _build_grade_result(checks, sql)


_TASK3_PROMPT = textwrap.dedent(
    """
    Write a **single** SQLite `SELECT` query (CTEs and window functions allowed, no DDL/DML) computing country-level sessionization and order attribution metrics.

    ### Schema
    ```sql
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        signup_date TEXT NOT NULL,
        country TEXT NOT NULL
    );

    CREATE TABLE page_views (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        viewed_at_sec INTEGER NOT NULL, -- epoch seconds
        page TEXT NOT NULL
    );

    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        ordered_at_sec INTEGER NOT NULL, -- epoch seconds
        amount_cents INTEGER NOT NULL,
        status TEXT NOT NULL -- 'completed' or 'cancelled'
    );
    ```

    ### Business Rules
    1. **Sessionization (Gaps & Islands)**:
       - Order each user's `page_views` by `viewed_at_sec ASC, id ASC`.
       - A new session begins whenever `viewed_at_sec - prev_viewed_at_sec > 1800` (a gap of exactly `1800` seconds stays in the **same** session).
       - For each session: `session_start = MIN(viewed_at_sec)`, `session_end = MAX(viewed_at_sec)`, `duration_sec = session_end - session_start`.
    2. **Order Attribution**:
       - Only orders with `status = 'completed'` qualify.
       - A completed order is attributed to a user's session if and only if `session_start <= ordered_at_sec <= session_end + 1800`. (You may assume attribution windows of a user's sessions do not overlap.)
       - Orders outside every session window (or belonging to users with 0 page views) are unattributed and excluded.
       - A single session with multiple attributed orders still counts as **1** `converted_sessions`, while summing all of its attributed orders into `attributed_revenue_cents`.
    3. **Output Columns** (one row per distinct `country` in `users`, ordered by `country ASC`):
       - `country`
       - `total_sessions` (`0` if none)
       - `avg_session_duration_sec` (`REAL` rounded to 1 decimal place; `0.0` if `total_sessions = 0`)
       - `converted_sessions` (`0` if none)
       - `attributed_revenue_cents` (`0` if none)
       - `session_conversion_rate_pct` (`converted_sessions * 100.0 / total_sessions` rounded to 1 decimal place; `0.0` if `total_sessions = 0`)

    Return ONLY a single SQL code block containing the query.
    """
).strip()

_TASK3_GOLDEN = textwrap.dedent(
    """
    WITH ordered_views AS (
        SELECT
            id,
            user_id,
            viewed_at_sec,
            CASE
                WHEN LAG(viewed_at_sec) OVER (PARTITION BY user_id ORDER BY viewed_at_sec ASC, id ASC) IS NULL THEN 1
                WHEN viewed_at_sec - LAG(viewed_at_sec) OVER (PARTITION BY user_id ORDER BY viewed_at_sec ASC, id ASC) > 1800 THEN 1
                ELSE 0
            END AS is_new_session
        FROM page_views
    ),
    tagged_views AS (
        SELECT
            user_id,
            viewed_at_sec,
            SUM(is_new_session) OVER (
                PARTITION BY user_id
                ORDER BY viewed_at_sec ASC, id ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS session_num
        FROM ordered_views
    ),
    sessions AS (
        SELECT
            user_id,
            session_num,
            MIN(viewed_at_sec) AS session_start,
            MAX(viewed_at_sec) AS session_end,
            MAX(viewed_at_sec) - MIN(viewed_at_sec) AS duration_sec
        FROM tagged_views
        GROUP BY user_id, session_num
    ),
    session_orders AS (
        SELECT
            s.user_id,
            s.session_num,
            s.duration_sec,
            CASE WHEN COUNT(o.id) > 0 THEN 1 ELSE 0 END AS is_converted,
            COALESCE(SUM(o.amount_cents), 0) AS revenue_cents
        FROM sessions s
        LEFT JOIN orders o
            ON o.user_id = s.user_id
            AND o.status = 'completed'
            AND o.ordered_at_sec BETWEEN s.session_start AND s.session_end + 1800
        GROUP BY s.user_id, s.session_num, s.duration_sec
    ),
    countries AS (
        SELECT DISTINCT country FROM users
    ),
    country_sessions AS (
        SELECT
            u.country,
            COUNT(so.session_num) AS total_sessions,
            COALESCE(ROUND(AVG(so.duration_sec), 1), 0.0) AS avg_session_duration_sec,
            COALESCE(SUM(so.is_converted), 0) AS converted_sessions,
            COALESCE(SUM(so.revenue_cents), 0) AS attributed_revenue_cents
        FROM users u
        JOIN session_orders so ON so.user_id = u.id
        GROUP BY u.country
    )
    SELECT
        c.country AS country,
        COALESCE(cs.total_sessions, 0) AS total_sessions,
        COALESCE(cs.avg_session_duration_sec, 0.0) AS avg_session_duration_sec,
        COALESCE(cs.converted_sessions, 0) AS converted_sessions,
        COALESCE(cs.attributed_revenue_cents, 0) AS attributed_revenue_cents,
        CASE
            WHEN COALESCE(cs.total_sessions, 0) = 0 THEN 0.0
            ELSE ROUND(cs.converted_sessions * 100.0 / cs.total_sessions, 1)
        END AS session_conversion_rate_pct
    FROM countries c
    LEFT JOIN country_sessions cs ON cs.country = c.country
    ORDER BY c.country ASC;
    """
).strip()


# ---------------------------------------------------------------------------
# Task 4: Constrained Hop Shortest Path Fix (Algorithms & Graph State)
# ---------------------------------------------------------------------------

_TASK4_CHECKS = [
    "src == dst returns 0",
    "max_stops == 0 allows direct flights only",
    "Visited-set Dijkstra pruning trap handled",
    "Exact stop-count trade-off across 1/2/3 stops",
    "Parallel directed edges select cheapest",
    "Cycles and zero-cost edges terminate cleanly",
    "Unreachable destination & directedness -> -1",
    "Multi-hop path cheaper than direct edge",
    "Diamond hub with multiple stop-cost pairs",
    "Scale test: 200 nodes, 2000 edges in < 0.5s",
]

_TASK4_TEST_RUNNER = textwrap.dedent(
    """
    import json
    import random
    import sys
    import time

    sys.path.insert(0, ".")
    checks = []

    def record(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "passed": True, "detail": detail or "ok"})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"})

    import solution
    cheapest_route = solution.cheapest_route

    def ref_bellman_ford(n, flights, src, dst, max_stops):
        if src == dst:
            return 0
        dist = [float("inf")] * n
        dist[src] = 0
        for _ in range(max_stops + 1):
            nxt = list(dist)
            for u, v, w in flights:
                if dist[u] != float("inf") and dist[u] + w < nxt[v]:
                    nxt[v] = dist[u] + w
            dist = nxt
        return -1 if dist[dst] == float("inf") else int(dist[dst])

    def t1():
        assert cheapest_route(3, [(0, 1, 5), (1, 0, 5)], 0, 0, 0) == 0
        assert cheapest_route(4, [], 2, 2, 2) == 0

    def t2():
        flights = [(0, 1, 10), (1, 2, 10), (0, 2, 50)]
        assert cheapest_route(3, flights, 0, 2, 0) == 50
        assert cheapest_route(3, [(0, 1, 10), (1, 2, 10)], 0, 2, 0) == -1

    def t3():
        # Hub node 2 is reached cheaply in 2 stops (cost 2), but max_stops=1 forbids continuing 2->3!
        # Slightly costlier 0->2 (cost 5, 0 stops) CAN continue 2->3 (cost 1) -> total 6.
        flights = [(0, 1, 1), (1, 4, 1), (4, 2, 0), (2, 3, 1), (0, 2, 5)]
        assert cheapest_route(5, flights, 0, 3, 1) == 6
        assert cheapest_route(5, flights, 0, 3, 3) == 3

    def t4():
        flights = [
            (0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1),
            (0, 2, 4), (2, 4, 3),
            (0, 4, 20),
        ]
        assert cheapest_route(5, flights, 0, 4, 0) == 20
        assert cheapest_route(5, flights, 0, 4, 1) == 7
        assert cheapest_route(5, flights, 0, 4, 2) == 5
        assert cheapest_route(5, flights, 0, 4, 3) == 4

    def t5():
        flights = [(0, 1, 50), (0, 1, 12), (0, 1, 30), (1, 2, 40), (1, 2, 5)]
        assert cheapest_route(3, flights, 0, 2, 1) == 17

    def t6():
        flights = [(0, 1, 0), (1, 0, 0), (1, 2, 0), (2, 1, 0), (2, 3, 7)]
        assert cheapest_route(4, flights, 0, 3, 1) == -1
        assert cheapest_route(4, flights, 0, 3, 2) == 7

    def t7():
        flights = [(2, 1, 5), (1, 0, 5), (3, 4, 1)]
        assert cheapest_route(5, flights, 0, 2, 4) == -1
        assert cheapest_route(5, flights, 0, 4, 4) == -1

    def t8():
        flights = [(0, 3, 100), (0, 1, 10), (1, 2, 10), (2, 3, 10)]
        assert cheapest_route(4, flights, 0, 3, 2) == 30

    def t9():
        flights = [
            (0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1),  # reaches 4 in 3 stops, cost 4
            (0, 4, 10),                                   # reaches 4 in 0 stops, cost 10
            (0, 5, 3), (5, 4, 3),                         # reaches 4 in 1 stop, cost 6
            (4, 6, 2), (6, 7, 2),
        ]
        assert cheapest_route(8, flights, 0, 7, 2) == 14   # 0->4->6->7 (2 stops)
        assert cheapest_route(8, flights, 0, 7, 3) == 10   # 0->5->4->6->7 (3 stops)
        assert cheapest_route(8, flights, 0, 7, 5) == 8    # 0->1->2->3->4->6->7 (5 stops)

    def t10():
        rng = random.Random(123)
        n = 200
        flights = []
        for _ in range(2000):
            u = rng.randrange(n)
            v = rng.randrange(n)
            if u != v:
                flights.append((u, v, rng.randint(1, 100)))
        expected = ref_bellman_ford(n, flights, 0, n - 1, 12)
        t0 = time.monotonic()
        got = cheapest_route(n, flights, 0, n - 1, 12)
        elapsed = time.monotonic() - t0
        assert got == expected, f"got={got}, expected={expected}"
        assert elapsed < 0.5, f"Too slow: {elapsed:.3f}s"
        return f"cost={got} in {elapsed:.4f}s"

    tests = [
        ("src == dst returns 0", t1),
        ("max_stops == 0 allows direct flights only", t2),
        ("Visited-set Dijkstra pruning trap handled", t3),
        ("Exact stop-count trade-off across 1/2/3 stops", t4),
        ("Parallel directed edges select cheapest", t5),
        ("Cycles and zero-cost edges terminate cleanly", t6),
        ("Unreachable destination & directedness -> -1", t7),
        ("Multi-hop path cheaper than direct edge", t8),
        ("Diamond hub with multiple stop-cost pairs", t9),
        ("Scale test: 200 nodes, 2000 edges in < 0.5s", t10),
    ]
    for name, fn in tests:
        record(name, fn)

    print("__EVAL_RESULT__:" + json.dumps({"checks": checks}))
    """
)


def _grade_task4(response_text: str) -> dict[str, Any]:
    code = extract_code_block(response_text, ("python", "py"))
    checks, _ = _run_python_tests(code, _TASK4_TEST_RUNNER, _TASK4_CHECKS)
    return _build_grade_result(checks, code)


_TASK4_PROMPT = textwrap.dedent(
    """
    The Python function `cheapest_route(n, flights, src, dst, max_stops)` below attempts to find the lowest-cost directed flight path from `src` to `dst` with **at most `max_stops` intermediate stops** (i.e. at most `max_stops + 1` edges), returning `0` if `src == dst` and `-1` if no valid route exists.

    However, it fails on several subtle graph topologies and performance edge cases. Diagnose and fix **all** bugs while keeping the exact function signature, and ensure a 200-node, 2,000-edge graph finishes in `< 0.5s`.

    ### Constraints
    - `1 <= n <= 500`
    - `flights` is a list of `(u, v, cost)` tuples with `0 <= u, v < n` and `cost >= 0` (parallel edges and cycles may exist).
    - `0 <= max_stops <= n`

    ### Buggy Code
    ```python
    import heapq


    def cheapest_route(
        n: int, flights: list[tuple[int, int, int]], src: int, dst: int, max_stops: int
    ) -> int:
        if src == dst:
            return 0
        adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
        for u, v, w in flights:
            adj[u].append((v, w))

        pq: list[tuple[int, int, int]] = [(0, src, 0)]
        visited: set[int] = set()
        while pq:
            cost, u, stops = heapq.heappop(pq)
            if u == dst:
                return cost
            if u in visited or stops > max_stops:
                continue
            visited.add(u)
            for v, w in adj[u]:
                heapq.heappush(pq, (cost + w, v, stops + 1))
        return -1
    ```

    Return ONLY a single Python code block containing the corrected `cheapest_route` function.
    """
).strip()

_TASK4_GOLDEN = textwrap.dedent(
    """
    import heapq


    def cheapest_route(
        n: int, flights: list[tuple[int, int, int]], src: int, dst: int, max_stops: int
    ) -> int:
        if src == dst:
            return 0
        adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
        for u, v, w in flights:
            adj[u].append((v, w))

        pq: list[tuple[int, int, int]] = [(0, src, 0)]
        best_stops = [max_stops + 2] * n
        while pq:
            cost, u, stops = heapq.heappop(pq)
            if u == dst:
                return cost
            if stops > max_stops or stops >= best_stops[u]:
                continue
            best_stops[u] = stops
            for v, w in adj[u]:
                if stops + 1 < best_stops[v]:
                    heapq.heappush(pq, (cost + w, v, stops + 1))
        return -1
    """
).strip()


# ---------------------------------------------------------------------------
# Task 5: 2D Knapsack + DAG + Mutual Exclusion Optimization
# ---------------------------------------------------------------------------

_TASK5_PROJECTS = {
    "P1":  {"w": 4, "c": 5, "v": 22, "deps": []},
    "P2":  {"w": 5, "c": 6, "v": 25, "deps": ["P1"]},
    "P3":  {"w": 6, "c": 4, "v": 18, "deps": []},
    "P4":  {"w": 3, "c": 8, "v": 24, "deps": ["P1"]},
    "P5":  {"w": 7, "c": 7, "v": 36, "deps": ["P2"]},
    "P6":  {"w": 4, "c": 9, "v": 27, "deps": ["P4"]},
    "P7":  {"w": 5, "c": 8, "v": 21, "deps": ["P3"]},
    "P8":  {"w": 6, "c": 3, "v": 23, "deps": []},
    "P9":  {"w": 3, "c": 4, "v": 16, "deps": ["P8"]},
    "P10": {"w": 4, "c": 6, "v": 26, "deps": ["P8", "P9"]},
}
_TASK5_EXCLUSIONS = [("P4", "P5"), ("P2", "P7"), ("P6", "P10")]
_TASK5_OPTIMAL_IMPACT = 114


def _grade_task5(response_text: str) -> dict[str, Any]:
    data, raw, parse_err = extract_json_payload(response_text)
    required = {"selected", "total_eng_weeks", "total_infra_cost", "total_impact"}

    checks: list[dict[str, Any]] = []
    valid_obj = isinstance(data, dict) and required.issubset(data.keys()) and isinstance(data.get("selected"), list)
    checks.append(
        {
            "name": "Valid JSON & required schema keys",
            "passed": valid_obj,
            "detail": "ok" if valid_obj else (parse_err or "Missing required keys or selected list"),
        }
    )

    if not valid_obj:
        for name in [
            "All selected IDs valid & unique",
            "DAG prerequisite dependencies satisfied",
            "Mutual exclusion rules respected",
            "Compliance rule: P3 or P7 selected",
            "Engineering capacity <= 24 weeks",
            "Infra budget <= 30 units",
            "Self-reported totals match selected set",
            "Near-optimal impact (>= 105 / 92%)",
            "Exact global optimum impact (114)",
        ]:
            checks.append({"name": name, "passed": False, "detail": "Invalid JSON"})
        return _build_grade_result(checks, raw)

    sel_list = data["selected"]
    all_str = all(isinstance(x, str) for x in sel_list)
    sel_set = set(sel_list) if all_str else set()
    ids_ok = all_str and len(sel_list) == len(sel_set) and sel_set.issubset(_TASK5_PROJECTS.keys()) and len(sel_set) > 0
    checks.append(
        {
            "name": "All selected IDs valid & unique",
            "passed": ids_ok,
            "detail": f"selected={sel_list!r}",
        }
    )

    dep_bad = []
    if ids_ok:
        for pid in sorted(sel_set):
            for dep in _TASK5_PROJECTS[pid]["deps"]:
                if dep not in sel_set:
                    dep_bad.append(f"{pid}->{dep}")
    checks.append(
        {
            "name": "DAG prerequisite dependencies satisfied",
            "passed": ids_ok and not dep_bad,
            "detail": "ok" if not dep_bad else ", ".join(dep_bad),
        }
    )

    excl_bad = [f"{a}+{b}" for a, b in _TASK5_EXCLUSIONS if a in sel_set and b in sel_set]
    checks.append(
        {
            "name": "Mutual exclusion rules respected",
            "passed": ids_ok and not excl_bad,
            "detail": "ok" if not excl_bad else ", ".join(excl_bad),
        }
    )

    comp_ok = bool(sel_set & {"P3", "P7"})
    checks.append(
        {
            "name": "Compliance rule: P3 or P7 selected",
            "passed": ids_ok and comp_ok,
            "detail": f"compliance={sorted(sel_set & {'P3', 'P7'})}",
        }
    )

    true_w = sum(_TASK5_PROJECTS[p]["w"] for p in sel_set) if ids_ok else 999
    true_c = sum(_TASK5_PROJECTS[p]["c"] for p in sel_set) if ids_ok else 999
    true_v = sum(_TASK5_PROJECTS[p]["v"] for p in sel_set) if ids_ok else 0

    checks.append(
        {
            "name": "Engineering capacity <= 24 weeks",
            "passed": ids_ok and true_w <= 24,
            "detail": f"true_eng_weeks={true_w}",
        }
    )
    checks.append(
        {
            "name": "Infra budget <= 30 units",
            "passed": ids_ok and true_c <= 30,
            "detail": f"true_infra_cost={true_c}",
        }
    )

    totals_ok = (
        ids_ok
        and data.get("total_eng_weeks") == true_w
        and data.get("total_infra_cost") == true_c
        and data.get("total_impact") == true_v
    )
    checks.append(
        {
            "name": "Self-reported totals match selected set",
            "passed": totals_ok,
            "detail": f"reported=({data.get('total_eng_weeks')},{data.get('total_infra_cost')},{data.get('total_impact')}), true=({true_w},{true_c},{true_v})",
        }
    )

    feasible = ids_ok and not dep_bad and not excl_bad and comp_ok and true_w <= 24 and true_c <= 30
    checks.append(
        {
            "name": "Near-optimal impact (>= 105 / 92%)",
            "passed": feasible and true_v >= 105,
            "detail": f"feasible={feasible}, impact={true_v}",
        }
    )
    checks.append(
        {
            "name": "Exact global optimum impact (114)",
            "passed": feasible and true_v == _TASK5_OPTIMAL_IMPACT,
            "detail": f"feasible={feasible}, impact={true_v}/{_TASK5_OPTIMAL_IMPACT}",
        }
    )

    return _build_grade_result(checks, json.dumps(data, indent=2))


_TASK5_PROMPT = textwrap.dedent(
    """
    Select an optimal subset of Q1 engineering projects from `P1..P10` to **maximize `total_impact`** subject to dual resource budgets, prerequisite dependencies, mutual exclusions, and compliance rules.

    ### Candidate Projects
    | ID | Eng Weeks | Infra Cost | Impact Score | Prerequisites (must also be selected) |
    | --- | ---: | ---: | ---: | --- |
    | `P1` | 4 | 5 | 22 | None |
    | `P2` | 5 | 6 | 25 | `P1` |
    | `P3` | 6 | 4 | 18 | None |
    | `P4` | 3 | 8 | 24 | `P1` |
    | `P5` | 7 | 7 | 36 | `P2` |
    | `P6` | 4 | 9 | 27 | `P4` |
    | `P7` | 5 | 8 | 21 | `P3` |
    | `P8` | 6 | 3 | 23 | None |
    | `P9` | 3 | 4 | 16 | `P8` |
    | `P10` | 4 | 6 | 26 | `P8`, `P9` |

    ### Hard Constraints
    1. **Budgets**: `total_eng_weeks <= 24` AND `total_infra_cost <= 30`.
    2. **Dependencies**: If a project is selected, all of its prerequisites must also be selected.
    3. **Mutual Exclusions** (cannot select both in any pair):
       - `{P4, P5}`
       - `{P2, P7}`
       - `{P6, P10}`
    4. **Compliance Mandate**: At least one of `{P3, P7}` must be selected.

    ### Output Format
    Return ONLY a single JSON object matching this exact schema:
    ```json
    {
      "selected": ["P1", "..."],
      "total_eng_weeks": 0,
      "total_infra_cost": 0,
      "total_impact": 0
    }
    ```
    """
).strip()

_TASK5_GOLDEN = json.dumps(
    {
        "selected": ["P1", "P3", "P4", "P6", "P8"],
        "total_eng_weeks": 23,
        "total_infra_cost": 29,
        "total_impact": 114,
    },
    indent=2,
)


# ---------------------------------------------------------------------------
# Task 6: Multi-Document Legal Precedence & SLA Billing Audit (Synthesis)
# ---------------------------------------------------------------------------

def _grade_task6(response_text: str) -> dict[str, Any]:
    data, raw, parse_err = extract_json_payload(response_text)
    required = {
        "sales_email_binding",
        "jan_gross_usd",
        "feb_gross_usd",
        "mar_gross_usd",
        "feb_unexcused_downtime_minutes",
        "mar_unexcused_downtime_minutes",
        "feb_sla_credit_usd",
        "mar_sla_credit_usd",
        "q1_total_credits_usd",
        "q1_net_payable_usd",
    }

    checks: list[dict[str, Any]] = []
    valid_obj = isinstance(data, dict) and required.issubset(data.keys())
    checks.append(
        {
            "name": "Valid JSON & all 10 audit fields",
            "passed": valid_obj,
            "detail": (
                "ok"
                if valid_obj
                else (parse_err or f"Missing: {sorted(required - set(data.keys())) if isinstance(data, dict) else type(data).__name__}")
            ),
        }
    )

    if not valid_obj:
        for name in [
            "Sales email rejected per integration clause",
            "January gross billing ($6,600)",
            "February gross billing ($7,650)",
            "March gross billing ($8,100)",
            "Feb unexcused downtime excludes customer SSO (140 min)",
            "Mar unexcused downtime includes late-notice maint (250 min)",
            "Feb SLA credit (10% of $7,650 = $765)",
            "Mar SLA credit (25% of $8,100 = $2,025)",
            "Q1 total credits ($2,790) & net payable ($19,560)",
        ]:
            checks.append({"name": name, "passed": False, "detail": "Invalid JSON"})
        return _build_grade_result(checks, raw)

    def num_eq(key: str, target: float) -> bool:
        val = data.get(key)
        return isinstance(val, (int, float)) and not isinstance(val, bool) and abs(float(val) - target) < 0.01

    checks.append(
        {
            "name": "Sales email rejected per integration clause",
            "passed": data.get("sales_email_binding") is False,
            "detail": f"got {data.get('sales_email_binding')!r}",
        }
    )
    checks.append(
        {
            "name": "January gross billing ($6,600)",
            "passed": num_eq("jan_gross_usd", 6600),
            "detail": f"got {data.get('jan_gross_usd')!r}",
        }
    )
    checks.append(
        {
            "name": "February gross billing ($7,650)",
            "passed": num_eq("feb_gross_usd", 7650),
            "detail": f"got {data.get('feb_gross_usd')!r}",
        }
    )
    checks.append(
        {
            "name": "March gross billing ($8,100)",
            "passed": num_eq("mar_gross_usd", 8100),
            "detail": f"got {data.get('mar_gross_usd')!r}",
        }
    )
    checks.append(
        {
            "name": "Feb unexcused downtime excludes customer SSO (140 min)",
            "passed": num_eq("feb_unexcused_downtime_minutes", 140),
            "detail": f"got {data.get('feb_unexcused_downtime_minutes')!r}",
        }
    )
    checks.append(
        {
            "name": "Mar unexcused downtime includes late-notice maint (250 min)",
            "passed": num_eq("mar_unexcused_downtime_minutes", 250),
            "detail": f"got {data.get('mar_unexcused_downtime_minutes')!r}",
        }
    )
    checks.append(
        {
            "name": "Feb SLA credit (10% of $7,650 = $765)",
            "passed": num_eq("feb_sla_credit_usd", 765),
            "detail": f"got {data.get('feb_sla_credit_usd')!r}",
        }
    )
    checks.append(
        {
            "name": "Mar SLA credit (25% of $8,100 = $2,025)",
            "passed": num_eq("mar_sla_credit_usd", 2025),
            "detail": f"got {data.get('mar_sla_credit_usd')!r}",
        }
    )
    checks.append(
        {
            "name": "Q1 total credits ($2,790) & net payable ($19,560)",
            "passed": num_eq("q1_total_credits_usd", 2790) and num_eq("q1_net_payable_usd", 19560),
            "detail": f"credits={data.get('q1_total_credits_usd')!r}, net={data.get('q1_net_payable_usd')!r}",
        }
    )

    return _build_grade_result(checks, json.dumps(data, indent=2))


_TASK6_PROMPT = textwrap.dedent(
    """
    Audit the Q1 2026 invoice reconciliation across the Master Services Agreement, Amendments, Sales correspondence, and Incident Telemetry below, and output strict JSON.

    ### Document 1: Master Services Agreement (Signed by both parties, effective 2025-06-01)
    - **Platform Fee**: `$4,800/month`, including up to `120` active seats.
    - **Seat Overages**: `$35/seat/month` for each seat above the included monthly seat allowance.
    - **Integration & Amendment Clause (Section 12.4)**: Only formal written Amendments signed by **both** Vendor VP of Finance and Customer General Counsel modify this Agreement. Emails, Slack messages, or single-party proposals have zero legal effect.
    - **SLA Guarantee**: `99.9%` monthly uptime, measured over the exact calendar minutes in that month (`days_in_month * 1440`; note 2026 is not a leap year).
    - **Downtime Exclusions**:
      1. Scheduled Maintenance announced to Customer at least **48 hours** in advance.
      2. Outages caused by **Customer-side misconfiguration** or expired Customer credentials.
    - **SLA Credit Tiers** (percentage of that month's total gross recurring invoice — platform fee + seat overages + active add-ons):
      - Monthly uptime `< target` and `>= 99.5%`: **10%** credit
      - Monthly uptime `< 99.5%` and `>= 99.0%`: **25%** credit
      - Monthly uptime `< 99.0%`: **50%** credit

    ### Document 2: Amendment 1 (Signed by both VP Finance & Customer GC, effective 2026-01-01)
    - Adds **Analytics Add-on** at `$1,200/month` flat fee.
    - Increases included seats from `120` to `150` and lowers the overage rate from `$35` to `$30/seat/month`.

    ### Document 3: Sales Email (2026-01-12, From: Sam Chen, Account Executive, To: Customer Ops)
    - "Hey team — as a Q1 goodwill gesture, we're waiving all seat overage fees for Jan–Mar 2026! Also feel free to assume a 99.95% SLA starting in January." (No formal amendment attached or signed by VP Finance / Customer GC.)

    ### Document 4: Amendment 2 (Signed by both VP Finance & Customer GC, effective 2026-02-01)
    - Upgrades base tier to **Enterprise Shield**: replaces the `$4,800` platform fee with `$6,000/month` and increases included seats to `180` (overage rate remains `$30/seat/month`; Analytics Add-on remains `$1,200/month`).
    - Upgrades the contractual monthly SLA Guarantee from `99.9%` to `99.95%` starting `2026-02-01` (same credit tier bands: `< 99.95% and >= 99.5%` = 10%, `< 99.5% and >= 99.0%` = 25%, `< 99.0%` = 50%).

    ### Document 5: Q1 2026 Seat Telemetry & Outage Log
    - **Active seats**:
      - January 2026: `170` seats
      - February 2026: `195` seats
      - March 2026: `210` seats
    - **Incidents**:
      - `2026-01-10`: Scheduled DB maintenance, `180` minutes (announced `72` hours prior).
      - `2026-01-22`: Vendor authentication outage, `30` minutes.
      - `2026-02-04`: Vendor API outage, `140` minutes.
      - `2026-02-18`: Customer SSO SAML certificate expired causing login failure, `90` minutes (Customer-side misconfiguration).
      - `2026-03-05`: Scheduled network switch maintenance, `60` minutes (announced `24` hours prior).
      - `2026-03-19`: Vendor storage outage, `190` minutes.

    ### Output JSON Schema
    Return ONLY a single JSON object with these exact keys (monetary values as integer or float USD):
    ```json
    {
      "sales_email_binding": false,
      "jan_gross_usd": 0,
      "feb_gross_usd": 0,
      "mar_gross_usd": 0,
      "feb_unexcused_downtime_minutes": 0,
      "mar_unexcused_downtime_minutes": 0,
      "feb_sla_credit_usd": 0,
      "mar_sla_credit_usd": 0,
      "q1_total_credits_usd": 0,
      "q1_net_payable_usd": 0
    }
    ```
    """
).strip()

_TASK6_GOLDEN = json.dumps(
    {
        "sales_email_binding": False,
        "jan_gross_usd": 6600,
        "feb_gross_usd": 7650,
        "mar_gross_usd": 8100,
        "feb_unexcused_downtime_minutes": 140,
        "mar_unexcused_downtime_minutes": 250,
        "feb_sla_credit_usd": 765,
        "mar_sla_credit_usd": 2025,
        "q1_total_credits_usd": 2790,
        "q1_net_payable_usd": 19560,
    },
    indent=2,
)


TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        id="trace-code-puzzles",
        title="Stateful Python Execution Tracing",
        short_label="Tracing",
        category="Code Execution Tracing",
        description="Mentally trace 10 tricky Python snippets covering aliasing, finally overrides, groupby, regex lookahead, dict keys, and rounding.",
        prompt=_TASK1_PROMPT,
        grader=_grade_task1,
        golden_solution=_TASK1_GOLDEN,
    ),
    BenchmarkTask(
        id="debug-async-cache",
        title="Bounded LRU+TTL Cache Multi-Bug Fix",
        short_label="Debug",
        category="Root-Cause Debugging",
        description="Diagnose and fix 6 interacting bugs in a byte-bounded LRU+TTL cache (without a bug hint list).",
        prompt=_TASK2_PROMPT,
        grader=_grade_task2,
        golden_solution=_TASK2_GOLDEN,
    ),
    BenchmarkTask(
        id="sql-cohort-sessions",
        title="Sessionization & Order Attribution SQL",
        short_label="SQL",
        category="Relational Analytics",
        description="Compute gaps-and-islands web sessions, 30-minute attribution windows, and conversion rates per country in SQLite.",
        prompt=_TASK3_PROMPT,
        grader=_grade_task3,
        golden_solution=_TASK3_GOLDEN,
    ),
    BenchmarkTask(
        id="algo-k-stops-routing",
        title="Bounded-Hops Shortest Flight Route",
        short_label="Algorithms",
        category="Graph Algorithms",
        description="Fix a visited-set Dijkstra bug for K-stop shortest paths across cycles, parallel edges, and a 200-node scale graph.",
        prompt=_TASK4_PROMPT,
        grader=_grade_task4,
        golden_solution=_TASK4_GOLDEN,
    ),
    BenchmarkTask(
        id="logic-weighted-knapsack-dag",
        title="2D Budget & DAG Project Optimizer",
        short_label="Planning",
        category="Combinatorial Optimization",
        description="Maximize impact across 10 projects under dual budgets, dependency DAG, mutual exclusions, and compliance rules.",
        prompt=_TASK5_PROMPT,
        grader=_grade_task5,
        golden_solution=_TASK5_GOLDEN,
    ),
    BenchmarkTask(
        id="synth-contract-audit",
        title="MSA Precedence & SLA Credit Audit",
        short_label="Audit",
        category="Multi-Doc Synthesis",
        description="Reconcile MSA amendments, non-binding sales email, seat overages, notice-window exclusions, and tiered SLA credits.",
        prompt=_TASK6_PROMPT,
        grader=_grade_task6,
        golden_solution=_TASK6_GOLDEN,
    ),
]

TASK_BY_ID: dict[str, BenchmarkTask] = {t.id: t for t in TASKS}


def _verify_task5_optimum() -> None:
    keys = list(_TASK5_PROJECTS.keys())
    best = -1
    count_best = 0
    for mask in range(1 << len(keys)):
        sel = {keys[i] for i in range(len(keys)) if mask & (1 << i)}
        if not (sel & {"P3", "P7"}):
            continue
        if any(a in sel and b in sel for a, b in _TASK5_EXCLUSIONS):
            continue
        if any(not set(_TASK5_PROJECTS[p]["deps"]).issubset(sel) for p in sel):
            continue
        w = sum(_TASK5_PROJECTS[p]["w"] for p in sel)
        c = sum(_TASK5_PROJECTS[p]["c"] for p in sel)
        v = sum(_TASK5_PROJECTS[p]["v"] for p in sel)
        if w <= 24 and c <= 30:
            if v > best:
                best = v
                count_best = 1
            elif v == best:
                count_best += 1
    assert best == _TASK5_OPTIMAL_IMPACT and count_best == 1, f"best={best}, count={count_best}"


def run_self_test() -> int:
    """Verify every golden solution scores 100.0 and broken input scores < 50.0."""
    _verify_task5_optimum()
    all_ok = True
    for task in TASKS:
        good = task.grade(task.golden_solution)
        bad = task.grade("sorry, I cannot complete this task.")
        status = "PASS" if (good["score"] == 100.0 and bad["score"] < 50.0) else "FAIL"
        print(
            f"[{status}] {task.id:<28} golden={good['score']:5.1f}% ({good['passed_checks']}/{good['total_checks']}) "
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
