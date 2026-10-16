"""Chunk 10 offline check: API guard, logging, run lock and the whole run_weekly flow, with fakes.

No internet, API key or Google account needed. It does not call the scheduler itself; see the README for how to
confirm the first real scheduled run.

Usage:
    python test_automation_offline.py
"""
import contextlib
import io
import itertools
import json
import logging
import os
import sys
import tempfile
import time
from datetime import date

import config

TMP = tempfile.mkdtemp()


def tmp(*parts):
    return os.path.join(TMP, *parts)


# every path the new code writes goes to a temp folder
config.LOG_DIR = tmp("logs")
config.RUN_STATUS_FILE = tmp("logs", "last_run.json")
config.RUN_HISTORY_FILE = tmp("logs", "run_history.jsonl")
config.RUN_LOCK_FILE = tmp("data", "run.lock")
config.API_USAGE_FILE = tmp("data", "api_usage.json")
config.ENRICHED_DATA_DIR = tmp("enriched")
config.DETAILS_CACHE_DIR = tmp("details_cache")

import details  # noqa: E402
import guard  # noqa: E402
import main  # noqa: E402
import run_weekly  # noqa: E402
import runtools  # noqa: E402
import search  # noqa: E402
import website  # noqa: E402
from cleaning import normalize_phone  # noqa: E402
from config import SHEET_COLUMNS  # noqa: E402
from models import Lead  # noqa: E402

details.DETAILS_CACHE_DIR = config.DETAILS_CACHE_DIR
website.ENRICHED_DATA_DIR = config.ENRICHED_DATA_DIR
time.sleep = lambda s: None          # the retry code sleeps; do not wait in a test


def new_guard(name, **kw):
    kw.setdefault("per_run", {"search": 3, "details": 5})
    kw.setdefault("per_month", {"search": 10, "details": 20})
    return guard.ApiGuard(tmp(f"{name}.json"), **kw)


# ---------------------------------------------------------------- 1. guard limits
print("1. Per-run limit stops BEFORE the request")
g = new_guard("g1")
for _ in range(3):
    g.charge("search")
try:
    g.charge("search")
    raise AssertionError("expected QuotaExceeded")
except guard.QuotaExceeded as e:
    assert "Per-run" in str(e) and "API_LIMITS_PER_RUN" in str(e) and e.stop_kind == "quota"
assert g.run_counts["search"] == 3, "a refused request must not be counted"
assert not isinstance(guard.QuotaExceeded("x"), RuntimeError), "must not be swallowed as 'one search failed'"

print("2. Monthly limit persists across runs and resets next month")
today = [date(2026, 10, 14)]
g1 = new_guard("g2", per_run={}, per_month={"details": 4}, today_fn=lambda: today[0])
for _ in range(3):
    g1.charge("details")
g2 = new_guard("g2", per_run={}, per_month={"details": 4}, today_fn=lambda: today[0])   # a new run, same file
g2.charge("details")
try:
    g2.charge("details")
    raise AssertionError("expected QuotaExceeded")
except guard.QuotaExceeded as e:
    assert "Monthly" in str(e) and "2026-10" in str(e)
today[0] = date(2026, 11, 2)
g3 = new_guard("g2", per_run={}, per_month={"details": 4}, today_fn=lambda: today[0])
g3.charge("details")
assert json.load(open(tmp("g2.json"))) == {"month": "2026-11", "details": 1}

print("3. Circuit breaker: failures in a row stop the run, one success resets")
g = new_guard("g3", max_consecutive_failures=3)
g.failed("403")
g.failed("403")
g.ok()
g.failed("500")
g.failed("500")
try:
    g.failed("API key not valid " + "x" * 1000)
    raise AssertionError("expected ApiCircuitOpen")
except guard.ApiCircuitOpen as e:
    assert e.stop_kind == "circuit" and "3 Google API requests failed in a row" in str(e) and len(str(e)) < 700

print("4. Unlimited kinds, damaged usage file, usage report")
g = new_guard("g4", per_run={}, per_month={})
for _ in range(50):
    g.charge("search")
open(tmp("g5.json"), "w").write("{not json")
g = new_guard("g5", per_run={"search": 2}, per_month={"search": 2})
g.charge("search")
assert json.load(open(tmp("g5.json")))["search"] == 1, "unreadable file starts again from zero"
u = g.usage()["search"]
assert (u["run"], u["run_limit"], u["month"], u["month_limit"]) == (1, 2, 1, 2)
assert "search 1/2 this run, 1/2 this month" in g.usage_line()

# ---------------------------------------------------------------- 5. the hooks in search.py / details.py
print("5. search.py and details.py respect the guard (fake HTTP)")


class Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code, self._p, self.text = code, payload or {}, text

    def json(self):
        return self._p


calls = {"post": 0, "get": 0}
script = {"post": [], "get": []}


def fake_post(*a, **k):
    calls["post"] += 1
    return script["post"].pop(0) if script["post"] else Resp(200, {"places": []})


def fake_get(*a, **k):
    calls["get"] += 1
    return script["get"].pop(0) if script["get"] else Resp(200, {"id": "x", "displayName": {"text": "X"}})


search.requests.post = fake_post
details.requests.get = fake_get

guard.set_guard(new_guard("h1", per_run={"search": 2, "details": 2}, per_month={}, max_consecutive_failures=3))
search._post_with_retry({})
search._post_with_retry({})
before = calls["post"]
try:
    search._post_with_retry({})
    raise AssertionError("expected QuotaExceeded")
except guard.QuotaExceeded:
    pass
assert calls["post"] == before, "no HTTP request may be made once the limit is reached"

details.get_place_details("PID1")
details.get_place_details("PID1")            # served from the on-disk cache: not a billed request
assert calls["get"] == 1 and guard.get_guard().run_counts["details"] == 1, "cache hits must not be charged"
details.get_place_details("PID2")
try:
    details.get_place_details("PID3")
    raise AssertionError("expected QuotaExceeded")
except guard.QuotaExceeded:
    pass

guard.set_guard(new_guard("h2", per_run={}, per_month={}, max_consecutive_failures=3))
script["post"] = [Resp(403, text="API key not valid")] * 10
for i in range(2):
    try:
        search._post_with_retry({})
    except RuntimeError as e:
        assert "403" in str(e)                       # the first failures are ordinary per-search failures
try:
    search._post_with_retry({})
    raise AssertionError("expected ApiCircuitOpen")
except guard.ApiCircuitOpen as e:
    assert "API key not valid" in str(e)

# ---------------------------------------------------------------- 6. pipeline stops cleanly on a guard stop
print("6. run_pipeline keeps finished batches and reports a guard stop")
phone_counter = itertools.count(1)
phones = {}


def phone_for(pid):
    if pid not in phones:
        while True:
            candidate = f"+4478{next(phone_counter):08d}"
            if normalize_phone(candidate, "United Kingdom")[2] == "ok":
                phones[pid] = candidate
                break
    return phones[pid]


class World:
    def __init__(self, stop_on_search=3, stop_exc=guard.QuotaExceeded):
        self.n, self.stop_on, self.exc = 0, stop_on_search, stop_exc

    def search(self, city, btype, country):
        self.n += 1
        if self.n >= self.stop_on:
            raise self.exc("simulated stop")
        return [{"place_id": f"{city}|{btype}|{k}", "name": f"{btype} {city} {k}", "address": f"{k} High St, {city}",
                 "search_city": city, "search_country": country, "search_business_type": btype,
                 "rating": 4.5, "reviews_count": 100 + k} for k in range(20)]

    def details(self, raw, limit=None):
        return [Lead(place_id=r["place_id"], business_type=r["search_business_type"], country=r["search_country"],
                     city=r["search_city"], business_name=r["name"], address=r["address"],
                     phone_number=phone_for(r["place_id"]), google_rating=r["rating"],
                     reviews_count=r["reviews_count"]) for r in raw]


class Sheet:
    def __init__(self):
        self.rows = [list(SHEET_COLUMNS)]

    def get_all_values(self):
        return self.rows

    def append_rows(self, values, **kw):
        self.rows += [list(v) for v in values]


def sheet_append(ws, leads):
    ws.append_rows([[l.get(c, "") for c in SHEET_COLUMNS] for l in leads], value_input_option="USER_ENTERED")


for exc, kind in ((guard.QuotaExceeded, "quota"), (guard.ApiCircuitOpen, "circuit")):
    world, ws, st = World(3, exc), Sheet(), tmp(f"state_{kind}.json")
    r = main.run_pipeline(ws, 100, ["United Kingdom"], cities=["London"], search_fn=world.search,
                          details_fn=world.details, enrich_fn=lambda l: l, append_fn=sheet_append,
                          sleep_fn=lambda s: None, log=lambda *a: None, state_path=st)
    assert r["stop_kind"] == kind and "simulated stop" in r["stop_reason"], r
    # search 1 adds 15 (per-query cap), search 2 adds 10 (per-city cap of 25), the third search is the one that stops
    assert r["added"] == 25 and len(ws.rows) == 26, "the two finished batches must be kept"
    assert r["shortfall"] == 75
    assert main.load_state(st, main.week_key())[0] == 25, "progress is saved so the next run continues"

world, ws = World(10 ** 6), Sheet()
r = main.run_pipeline(ws, 10, ["United Kingdom"], cities=["Leeds"], search_fn=world.search, details_fn=world.details,
                      enrich_fn=lambda l: l, append_fn=sheet_append, sleep_fn=lambda s: None, log=lambda *a: None,
                      state_path=tmp("state_ok.json"))
assert r["stop_kind"] is None and r["stop_reason"] is None and r["added"] == 10, "a normal run has no stop reason"

# ---------------------------------------------------------------- 7. logging captures print(), including non-Latin text
print("7. Logging: print() output lands in the file, Arabic survives, streams are restored")
logger = runtools.setup_logging(config.LOG_DIR, "t7.log", 100_000, 2, console=False)
real_out, real_err = sys.stdout, sys.stderr
with runtools.capture_output(logger):
    print("  [1/3] سوبر ماركت النور")
    print("partial ", end="")
    print("line")
    sys.stderr.write("a warning\n")
    logging.getLogger("mart").info("direct record")
assert sys.stdout is real_out and sys.stderr is real_err
text = open(os.path.join(config.LOG_DIR, "t7.log"), encoding="utf-8").read()
assert "سوبر ماركت النور" in text and "partial line" in text and "direct record" in text
assert "WARNING a warning" in text, "stderr output is logged as WARNING"
assert "INFO" in text and "[1/3] سوبر ماركت النور" in text

print("   rotation keeps the log from growing forever")
logger = runtools.setup_logging(config.LOG_DIR, "rot.log", 2000, 2, console=False)
for i in range(300):
    logger.info("line %d %s", i, "x" * 40)
rotated = sorted(f for f in os.listdir(config.LOG_DIR) if f.startswith("rot.log"))
assert rotated == ["rot.log", "rot.log.1", "rot.log.2"], rotated

print("   a console that cannot print Arabic does not crash the run")


class Cp1252Out:
    """A stdout like a Windows console in cp1252, which raises on Arabic unless errors='replace' is set."""

    def __init__(self):
        self.buf, self.errors = [], "strict"

    def reconfigure(self, errors=None, **kw):
        self.errors = errors or self.errors

    def write(self, s):
        self.buf.append(s.encode("cp1252", errors=self.errors).decode("cp1252"))
        return len(s)

    def flush(self):
        pass


fake_console = Cp1252Out()
saved = sys.stdout
sys.stdout = fake_console
try:
    logger = runtools.setup_logging(config.LOG_DIR, "t7b.log", 100_000, 2, console=True)
    with runtools.capture_output(logger):
        print("Added: سوبر ماركت")
finally:
    sys.stdout = saved
assert "Added" in "".join(fake_console.buf) and "?" in "".join(fake_console.buf)
assert "سوبر ماركت" in open(os.path.join(config.LOG_DIR, "t7b.log"), encoding="utf-8").read()

# ---------------------------------------------------------------- 8. run lock
print("8. Run lock")
lock_path = tmp("locks", "a.lock")
a, b = runtools.RunLock(lock_path, 6), runtools.RunLock(lock_path, 6)
assert a.acquire() and not b.acquire() and "pid" in b.holder()
a.release()
assert not os.path.exists(lock_path) and b.acquire()
b.release()
open(lock_path, "w").write("{}")
old = time.time() - 7 * 3600
os.utime(lock_path, (old, old))
c = runtools.RunLock(lock_path, 6)
msgs = []
assert c.acquire(log=msgs.append) and "stale" in msgs[0].lower(), "a crashed run's old lock must not block forever"
c.release()
c.release()                                   # releasing twice is harmless

# ---------------------------------------------------------------- 9. run_weekly end to end (fakes)
print("9. run_weekly: exit codes, status files, lock, extras")


class FakeWs:
    pass


def summary(**kw):
    base = {"week": "2026-W42", "target": 100, "added": 100, "week_total": 100, "queries": 12, "shortfall": 0,
            "leads": [], "dry_run": False, "stop_reason": None, "stop_kind": None, "interrupted": False}
    base.update(kw)
    return base


def weekly(pipeline, argv=("--target", "100"), open_sheet=None, refresh=None):
    refreshed = []
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = run_weekly.run(list(argv), open_sheet=open_sheet or (lambda dry: None if dry else FakeWs()),
                              pipeline=pipeline, refresh_extras=refresh or (lambda ws: refreshed.append(ws)),
                              sleep_fn=lambda s: None)
    status = json.load(open(config.RUN_STATUS_FILE, encoding="utf-8"))
    return code, status, refreshed


def log_text():
    return open(os.path.join(config.LOG_DIR, config.LOG_FILE), encoding="utf-8").read()


def print_and_return(**kw):
    def pipeline(ws, target, countries, **k):
        print("  added 100 leads: سوبر ماركت")
        return summary(**kw)
    return pipeline


code, st, refreshed = weekly(print_and_return())
assert code == 0 and st["status"] == "ok" and st["added_this_run"] == 100 and st["week_total"] == 100, st
assert len(refreshed) == 1, "sheet extras are refreshed after leads were added"
assert "api_usage" in st and st["started_at"] and st["finished_at"] and st["dry_run"] is False
assert "سوبر ماركت" in log_text() and "Run finished: OK" in log_text(), "pipeline print() output is in the log"
assert not os.path.exists(config.RUN_LOCK_FILE), "lock released"
history = open(config.RUN_HISTORY_FILE, encoding="utf-8").read().strip().splitlines()
assert len(history) == 1 and json.loads(history[0])["status"] == "ok"

seen_args = {}


def capture(ws, target, countries, **k):
    seen_args.update(target=target, countries=countries, **k)
    return summary(added=5, week_total=5, target=target)


code, st, _ = weekly(capture, argv=["--target", "5", "--countries", "uk", "--cities", "Leeds, London",
                                    "--types", "Mini Mart", "--dry-run"])
assert seen_args["target"] == 5 and seen_args["countries"] == ["United Kingdom"]
assert seen_args["cities"] == ["Leeds", "London"] and seen_args["types"] == ["Mini Mart"] and seen_args["dry_run"]

code, st, refreshed = weekly(lambda *a, **k: summary(added=5, week_total=5), argv=["--dry-run"])
assert code == 0 and refreshed == [], "a dry run never touches the sheet"

code, st, refreshed = weekly(lambda *a, **k: summary(added=0, week_total=100))
assert code == 0 and "Nothing to do" in st["message"] and refreshed == []

code, st, _ = weekly(lambda *a, **k: summary(added=60, week_total=60, shortfall=40))
assert code == 1 and st["status"] == "short" and "short by 40" in st["message"]
assert "WARNING Run finished: SHORT" in log_text(), "problems are logged above INFO so they can be grepped"

code, st, _ = weekly(lambda *a, **k: summary(added=30, week_total=30, shortfall=70, stop_kind="quota",
                                              stop_reason="Monthly limit reached"))
assert code == 3 and st["status"] == "quota" and "Monthly limit reached" in st["message"] and "30 leads" in st["message"]

code, st, _ = weekly(lambda *a, **k: summary(added=0, shortfall=100, stop_kind="circuit", stop_reason="403 in a row"))
assert code == 2 and st["status"] == "error" and "403 in a row" in st["message"]

code, st, _ = weekly(lambda *a, **k: summary(added=10, week_total=10, shortfall=90, interrupted=True))
assert code == 130 and st["status"] == "interrupted"


def raise_(exc):
    def f(*a, **k):
        raise exc
    return f


code, st, _ = weekly(raise_(main.PipelineError("The sheet's header row is missing")))
assert code == 2 and "header row" in st["message"] and not os.path.exists(config.RUN_LOCK_FILE)
assert "ERROR   Run finished: ERROR" in log_text()

code, st, _ = weekly(print_and_return(), open_sheet=raise_(SystemExit("ERROR: 'credentials.json' not found.")))
assert code == 2 and "credentials.json" in st["message"] and "STOPPED" in log_text()

code, st, _ = weekly(raise_(ZeroDivisionError("boom")))
assert code == 2 and "ZeroDivisionError" in st["message"] and "Traceback" in log_text()
assert not os.path.exists(config.RUN_LOCK_FILE), "lock is released even after a crash"

failing_extras_calls = []


def failing_extras(ws):
    failing_extras_calls.append(1)
    raise RuntimeError("Sheets quota")


code, st, _ = weekly(print_and_return(), refresh=failing_extras)
assert code == 0 and failing_extras_calls and "Could not refresh the sheet extras" in log_text(), \
    "a failed cosmetic refresh must not fail the run or hide saved leads"

print("   opening the sheet is retried (real _open_sheet path, stand-in sheets module), a missing id fails clearly")
import types  # noqa: E402

attempts = []


def flaky_get_worksheet():
    attempts.append(1)
    if len(attempts) < 3:
        raise SystemExit("ERROR: could not open the sheet (503)")      # what sheets.get_spreadsheet() does on API errors
    return FakeWs()


fake_sheets = types.ModuleType("sheets")
fake_sheets.get_worksheet = flaky_get_worksheet
real_sheets = sys.modules.get("sheets")
sys.modules["sheets"] = fake_sheets
saved_id = config.GOOGLE_SHEET_ID
config.GOOGLE_SHEET_ID = "sheet-id-123"
try:
    sleeps = []
    assert isinstance(run_weekly._open_sheet(False, sleep_fn=sleeps.append), FakeWs)
    assert len(attempts) == 3 and sleeps == [5, 10], (len(attempts), sleeps)

    attempts.clear()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = run_weekly.run(["--target", "100"], pipeline=print_and_return(), refresh_extras=lambda ws: None,
                              sleep_fn=lambda s: None)
    assert code == 0 and len(attempts) == 3, "run() opens the sheet through the retry"

    always_down = lambda: (_ for _ in ()).throw(SystemExit("still down"))   # noqa: E731
    fake_sheets.get_worksheet = always_down
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = run_weekly.run(["--target", "100"], pipeline=print_and_return(), refresh_extras=lambda ws: None,
                              sleep_fn=lambda s: None)
    st = json.load(open(config.RUN_STATUS_FILE, encoding="utf-8"))
    assert code == 2 and "still down" in st["message"], "after 3 tries the run fails with the sheet's own message"
finally:
    config.GOOGLE_SHEET_ID = saved_id
    if real_sheets is None:
        sys.modules.pop("sheets", None)
    else:
        sys.modules["sheets"] = real_sheets

saved_id = config.GOOGLE_SHEET_ID
config.GOOGLE_SHEET_ID = None
try:
    try:
        run_weekly._open_sheet(False)
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert "GOOGLE_SHEET_ID" in str(e)
    assert run_weekly._open_sheet(True) is None
finally:
    config.GOOGLE_SHEET_ID = saved_id

print("10. run_weekly refuses to overlap, and replaces a stale lock")
calls_made = []
holder = runtools.RunLock(config.RUN_LOCK_FILE, 6)
assert holder.acquire()
history_before = len(open(config.RUN_HISTORY_FILE, encoding="utf-8").read().splitlines())
code, st, _ = weekly(lambda *a, **k: calls_made.append(1) or summary())
assert code == 4 and st["status"] == "locked" and not calls_made and "already in progress" in st["message"]
assert os.path.exists(config.RUN_LOCK_FILE), "the other run's lock must not be removed by the skipped run"
assert len(open(config.RUN_HISTORY_FILE, encoding="utf-8").read().splitlines()) == history_before, \
    "a skipped duplicate is not recorded as a run"
old = time.time() - 7 * 3600
os.utime(config.RUN_LOCK_FILE, (old, old))
code, st, _ = weekly(lambda *a, **k: calls_made.append(1) or summary())
assert code == 0 and calls_made, "a 7 hour old lock is a crashed run: it is replaced"
holder.held = False

print("11. run_weekly resets the per-run API counters each run")
guard.set_guard(new_guard("g11", per_run={"search": 1}, per_month={}))
guard.charge("search")


def uses_search(ws, target, countries, **k):
    guard.charge("search")      # would raise if the counter were not reset at the start of the run
    return summary()


code, st, _ = weekly(uses_search)
assert code == 0, st

print("12. main() switches to the project folder first (cron and Task Scheduler start elsewhere)")
here, seen_cwd, real_run = os.getcwd(), [], run_weekly.run
run_weekly.run = lambda argv=None, **k: seen_cwd.append(os.getcwd()) or 0
os.chdir(tempfile.gettempdir())
try:
    assert run_weekly.main([]) == 0
finally:
    run_weekly.run = real_run
    os.chdir(here)
assert os.path.samefile(seen_cwd[0], run_weekly.PROJECT_DIR), seen_cwd

print("\nChunk 10 automation offline check passed.")
