"""Chunk 10: Google API guard. Quota limits and a circuit breaker for the billed Places requests.

search.py and details.py call three functions right where they make a real HTTP request:

    guard.charge("search")      # before the request: raises QuotaExceeded if a limit would be crossed
    guard.ok()                  # after a successful request
    guard.failed(error_text)    # after a request that failed for good (retries used up)

Limits come from config.py (API_LIMITS_PER_RUN, API_LIMITS_PER_MONTH). The monthly count lives in
data/api_usage.json and starts again from zero when the calendar month changes. The per-run count lives in memory.

GuardStop is deliberately NOT a RuntimeError: the pipeline treats RuntimeError as "this one search/place failed,
skip it", whereas a guard stop must end the whole run. main.run_pipeline catches GuardStop, keeps everything already
written to the sheet, and reports why it stopped.
"""
import json
import os
import threading
from collections import Counter
from datetime import date

import config


class GuardStop(Exception):
    """Base class: the run must stop now. `stop_kind` is 'quota' or 'circuit'."""
    stop_kind = "guard"


class QuotaExceeded(GuardStop):
    stop_kind = "quota"


class ApiCircuitOpen(GuardStop):
    stop_kind = "circuit"


class ApiGuard:
    def __init__(self, usage_file, per_run=None, per_month=None, max_consecutive_failures=6, today_fn=date.today):
        self.usage_file = usage_file
        self.per_run = dict(per_run or {})
        self.per_month = dict(per_month or {})
        self.max_failures = max_consecutive_failures
        self.today_fn = today_fn
        self.run_counts: Counter = Counter()
        self.failures = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ monthly file
    def _month(self) -> str:
        return self.today_fn().strftime("%Y-%m")

    def _load_month(self) -> dict:
        try:
            with open(self.usage_file, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            print(f"  warning: {self.usage_file} is unreadable, the monthly API count starts again from zero")
            data = {}
        if data.get("month") != self._month():
            data = {"month": self._month()}
        return data

    def _save_month(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.usage_file) or ".", exist_ok=True)
        tmp = f"{self.usage_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.usage_file)

    # ------------------------------------------------------------ the three hooks
    def charge(self, kind: str, n: int = 1) -> None:
        """Count `n` billed requests of this kind, or raise QuotaExceeded BEFORE the request is made."""
        with self._lock:
            run_limit = self.per_run.get(kind)
            if run_limit is not None and self.run_counts[kind] + n > run_limit:
                raise QuotaExceeded(
                    f"Per-run limit reached for Google '{kind}' requests ({self.run_counts[kind]} used, limit "
                    f"{run_limit}). Raise API_LIMITS_PER_RUN['{kind}'] in config.py if this is expected.")
            data = self._load_month()
            used = int(data.get(kind, 0))
            month_limit = self.per_month.get(kind)
            if month_limit is not None and used + n > month_limit:
                raise QuotaExceeded(
                    f"Monthly limit reached for Google '{kind}' requests ({used} used in {data['month']}, limit "
                    f"{month_limit}). It resets next month. Raise API_LIMITS_PER_MONTH['{kind}'] in config.py "
                    "if you want to spend more.")
            data[kind] = used + n
            self._save_month(data)
            self.run_counts[kind] += n

    def ok(self) -> None:
        self.failures = 0

    def failed(self, error) -> None:
        """A request failed for good. Too many in a row means something is wrong with the setup, not one place."""
        with self._lock:
            self.failures += 1
            if self.failures >= self.max_failures:
                raise ApiCircuitOpen(
                    f"{self.failures} Google API requests failed in a row (last error: {str(error)[:300]}). "
                    "Stopping the run. Usual causes: invalid or restricted API key, billing not enabled, "
                    "API not enabled for the project, or no internet.")

    # ------------------------------------------------------------ reporting
    def usage(self) -> dict:
        """{'search': {'run': 21, 'run_limit': 150, 'month': 63, 'month_limit': 600}, 'details': {...}}"""
        month = self._load_month()
        kinds = set(self.run_counts) | set(self.per_run) | set(self.per_month) | (set(month) - {"month"})
        return {k: {"run": self.run_counts[k], "run_limit": self.per_run.get(k),
                    "month": int(month.get(k, 0)), "month_limit": self.per_month.get(k)}
                for k in sorted(kinds)}

    def usage_line(self) -> str:
        def lim(v):
            return "no limit" if v is None else v
        parts = [f"{k} {u['run']}/{lim(u['run_limit'])} this run, {u['month']}/{lim(u['month_limit'])} this month"
                 for k, u in self.usage().items()]
        return "Google API requests: " + "; ".join(parts) if parts else "Google API requests: none made"


# ---------------------------------------------------------------- the process-wide guard used by search.py / details.py
_guard: ApiGuard | None = None


def get_guard() -> ApiGuard:
    global _guard
    if _guard is None:
        _guard = ApiGuard(config.API_USAGE_FILE, config.API_LIMITS_PER_RUN, config.API_LIMITS_PER_MONTH,
                          config.API_MAX_CONSECUTIVE_FAILURES)
    return _guard


def set_guard(guard: ApiGuard | None) -> None:
    """Replace the process-wide guard (run_weekly and the tests do this). None = rebuild from config next time."""
    global _guard
    _guard = guard


def charge(kind: str, n: int = 1) -> None:
    get_guard().charge(kind, n)


def ok() -> None:
    get_guard().ok()


def failed(error) -> None:
    get_guard().failed(error)
