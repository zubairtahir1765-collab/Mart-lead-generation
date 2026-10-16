"""Chunk 9 offline check: the pipeline logic with a fake search, fake Places details and a fake sheet.

No internet, API key or Google account needed. Checks: stops at the target, skips leads already in the
sheet, respects the caps, resumes within a week, dry run writes nothing, IDs continue, bad writes are safe.

Usage:
    python test_pipeline_offline.py
"""
import itertools
import os
import tempfile

import config

TMP = tempfile.mkdtemp()
config.ENRICHED_DATA_DIR = os.path.join(TMP, "enriched")
import website  # noqa: E402

website.ENRICHED_DATA_DIR = config.ENRICHED_DATA_DIR

import main  # noqa: E402
from cleaning import normalize_phone  # noqa: E402
from config import SHEET_COLUMNS  # noqa: E402
from models import Lead  # noqa: E402

COUNTRY_PREFIX = {"United Kingdom": "+44780", "UAE": "+97150", "Pakistan": "+92300"}


# ---------------------------------------------------------------- fakes
class Universe:
    """Pretend Google: 20 places per query (2 without a phone). Same place = same phone, always."""

    def __init__(self, per_query=20, no_phone=2):
        self.per_query, self.no_phone = per_query, no_phone
        self.phones, self.counters = {}, {}
        self.search_calls, self.details_calls = [], []

    def phone_for(self, pid, country):
        if pid not in self.phones:
            counter = self.counters.setdefault(country, itertools.count(1))
            while True:   # only keep numbers the real validator accepts, whatever the library version
                candidate = f"{COUNTRY_PREFIX[country]}{next(counter):07d}"
                if normalize_phone(candidate, country)[2] == "ok":
                    self.phones[pid] = candidate
                    break
        return self.phones[pid]

    def search(self, city, btype, country):
        self.search_calls.append((country, city, btype))
        return [{"place_id": f"{country}|{city}|{btype}|{k}", "name": f"{btype} {city} {k}",
                 "address": f"{k} High St, {city}", "search_city": city, "search_country": country,
                 "search_business_type": btype, "rating": 4.5, "reviews_count": 200 - k}
                for k in range(self.per_query)]

    def details(self, raw, limit=None):
        out = []
        for r in (raw[:limit] if limit else raw):
            self.details_calls.append(r["place_id"])
            k = int(r["place_id"].rsplit("|", 1)[1])
            has_phone = k >= self.no_phone
            phone = self.phone_for(r["place_id"], r["search_country"]) if has_phone else ""
            out.append(Lead(place_id=r["place_id"], business_type=r["search_business_type"],
                            country=r["search_country"], city=r["search_city"], business_name=r["name"],
                            address=r["address"], phone_number=phone, google_rating=r["rating"],
                            reviews_count=r["reviews_count"]))
        return out

    def row_for(self, pid, lead_id):
        """A row as it would already sit in the sheet (international phone format, like Chunk 5 writes)."""
        country, city, btype, k = pid.split("|")
        e164 = self.phone_for(pid, country)
        intl = normalize_phone(e164, country)[1]
        row = dict.fromkeys(SHEET_COLUMNS, "")
        row.update({"Lead ID": lead_id, "Business Name": f"{btype} {city} {k}", "Country": country,
                    "City": city, "Address": f"{k} High St, {city}", "Phone Number": intl})
        return [row[c] for c in SHEET_COLUMNS]


class FakeSheet:
    def __init__(self, rows=None, header=None):
        self.rows = [list(header or SHEET_COLUMNS)] + [list(r) for r in (rows or [])]
        self.append_calls = 0

    def get_all_values(self):
        return [[str(c) for c in r] for r in self.rows]

    def append_rows(self, values, **kwargs):
        assert kwargs.get("value_input_option") == "USER_ENTERED"
        self.append_calls += 1
        self.rows += [list(r) for r in values]

    def data(self):
        return [dict(zip(SHEET_COLUMNS, r)) for r in self.rows[1:]]


def sheet_append(ws, leads):          # same shape sheets.append_leads gives the sheet
    ws.append_rows([[l.get(c, "") for c in SHEET_COLUMNS] for l in leads],
                   value_input_option="USER_ENTERED", table_range="A1")


def run(ws, uni, state, **kw):
    kw.setdefault("countries", ["United Kingdom"])
    kw.setdefault("target", 100)
    return main.run_pipeline(ws, search_fn=uni.search, details_fn=uni.details, enrich_fn=lambda leads: leads,
                             append_fn=sheet_append, sleep_fn=lambda s: None, log=lambda *a: None,
                             state_path=state, **kw)


def state_file(name):
    return os.path.join(TMP, f"{name}.json")


# ---------------------------------------------------------------- 1. stops at the target, skips what is in the sheet
print("1. Skips leads already in the sheet, stops exactly at the target")
uni = Universe()
existing = [uni.row_for(f"United Kingdom|London|Supermarket|{k}", f"LD-{k - 1:04d}") for k in (3, 4, 5)]
ws = FakeSheet(existing)
st = state_file("t1")
r = run(ws, uni, st, target=10, cities=["London"])
data = ws.data()
assert r["added"] == 10 and r["shortfall"] == 0 and len(data) == 3 + 10, r
assert r["queries"] == 1, "18 usable places in the first search, so one search is enough"
old_phones = {row[SHEET_COLUMNS.index("Phone Number")] for row in existing}
assert not old_phones & {d["Phone Number"] for d in data[3:]}, "a lead from the sheet was added again"
ids = [d["Lead ID"] for d in data]
assert ids[:3] == ["LD-0002", "LD-0003", "LD-0004"] and ids[3] == "LD-0005", ids
assert len(set(ids)) == len(ids)

# ---------------------------------------------------------------- 2. IDs, rows and fields
print("2. IDs continue, every row is complete")
nums = [int(i[3:]) for i in ids]
assert nums[3:] == list(range(max(nums[:3]) + 1, max(nums[:3]) + 11)), nums
for d in data[3:]:
    assert d["Lead Priority"] in ("High", "Medium", "Low"), d
    assert d["Call Status"] == "Not Called" and d["Follow-up Date"] == ""
    assert d["Order Method"] and d["Delivery System"] and d["Automation Status"]
    assert d["Phone Number"].startswith("+44") and d["Business Name"] and d["City"] == "London"
assert len({d["Phone Number"] for d in data}) == len(data), "duplicate phone in the sheet"

# ---------------------------------------------------------------- 3. second run in the same week
print("3. Same week: nothing to do, then only the missing leads")
before = len(uni.search_calls)
r = run(ws, uni, st, target=10, cities=["London"])
assert r["added"] == 0 and len(uni.search_calls) == before and ws.append_calls == 1
first_query_ids = {f"United Kingdom|London|Supermarket|{k}" for k in range(20)}
uni.details_calls.clear()
r = run(ws, uni, st, target=14, cities=["London"])
assert r["added"] == 4 and len(ws.data()) == 3 + 14, r
processed_again = {p for p in uni.details_calls if p in first_query_ids}
# only the 5 places the cap trimmed in run 1 (lowest reviews) come back; added/rejected/skipped ones are not re-fetched
assert processed_again == {f"United Kingdom|London|Supermarket|{k}" for k in range(15, 20)}, processed_again

# ---------------------------------------------------------------- 4. caps spread leads over cities
print("4. Per-query and per-city caps")
uni = Universe()
ws = FakeSheet()
r = run(ws, uni, state_file("t4"), target=40, max_per_query=15, max_per_city=25)
by_city = {}
for d in ws.data():
    by_city[d["City"]] = by_city.get(d["City"], 0) + 1
assert by_city == {"London": 25, "Manchester": 15}, by_city
assert uni.search_calls[0] == ("United Kingdom", "London", "Supermarket")
assert uni.search_calls[1] == ("United Kingdom", "London", "Mini Mart"), "loops go types inside cities"

# ---------------------------------------------------------------- 5. relaxes caps when short
print("5. Caps are relaxed when the target is still not met")
uni = Universe()
ws = FakeSheet()
r = run(ws, uni, state_file("t5"), target=100, cities=["London"], max_per_query=15, max_per_city=25)
assert r["added"] == 100 and r["shortfall"] == 0 and len(ws.data()) == 100, r
assert len({d["Phone Number"] for d in ws.data()}) == 100

# ---------------------------------------------------------------- 6. dry run
print("6. Dry run writes nothing")
uni = Universe()
ws = FakeSheet()
st = state_file("t6")
r = run(ws, uni, st, target=12, cities=["Leeds"], dry_run=True)
assert r["added"] == 12 and ws.append_calls == 0 and not ws.data() and not os.path.exists(st)

# ---------------------------------------------------------------- 7. shortfall is reported
print("7. Shortfall when searches run dry")
uni = Universe(per_query=5, no_phone=0)
ws = FakeSheet()
r = run(ws, uni, state_file("t7"), target=100, cities=["Leeds"], types=["Supermarket", "Mini Mart"])
assert r["added"] == 10 and r["shortfall"] == 90, r

# ---------------------------------------------------------------- 8. search limit
print("8. Search limit per run")
uni = Universe(per_query=3, no_phone=0)
ws = FakeSheet()
r = run(ws, uni, state_file("t8"), target=100, max_queries=4)
assert r["queries"] == 4 and len(uni.search_calls) == 4

# ---------------------------------------------------------------- 9. failed writes are safe
print("9. A failed sheet write stops the run and is not counted")
uni = Universe()
ws = FakeSheet()
st = state_file("t9")


def broken_append(ws_, leads):
    raise ConnectionError("sheet is down")


try:
    main.run_pipeline(ws, 10, ["United Kingdom"], cities=["Leeds"], search_fn=uni.search, details_fn=uni.details,
                      enrich_fn=lambda l: l, append_fn=broken_append, sleep_fn=lambda s: None, log=lambda *a: None,
                      state_path=st)
    raise AssertionError("expected PipelineError")
except main.PipelineError as e:
    assert "sheet is down" in str(e) and "saved in" in str(e)
assert main.load_state(st, main.week_key()) == (0, set()), "failed batch must not be marked as done"

# ---------------------------------------------------------------- 10. bad sheet header
print("10. Sheet not set up")
try:
    run(FakeSheet(header=["Name", "Phone"]), Universe(), state_file("t10"), target=5)
    raise AssertionError("expected PipelineError")
except main.PipelineError as e:
    assert "setup_sheet.py" in str(e)

# ---------------------------------------------------------------- 11. reading the sheet
print("11. Reading existing leads")
rows = [SHEET_COLUMNS,
        ["LD-0007", "Raj Mart Ltd", "Grocery Store", "United Kingdom", "Leeds", "5 Mill Rd, Leeds", "+44 7800 000001"] + [""] * 14,
        ["LD-0012", "Al Noor", "Mini Mart", "Pakistan", "Lahore", "Main Blvd", "923001234567"] + [""] * 14,
        ["TEST", "TEST - Dummy Mart", "", "United Kingdom", "Leeds", "1 Test Street", "+44 113 000 0000"] + [""] * 14]
phones, name_addr, max_id = main.read_sheet_keys(rows)
assert max_id == 12 and "+447800000001" in phones
assert f"{main.name_key('Raj Mart')}|{main.address_key('5 Mill Rd, Leeds')}" in name_addr, "'Ltd' must not matter"
assert main.read_sheet_keys([]) == (set(), set(), 0)

# ---------------------------------------------------------------- 12. command line helpers
print("12. Command line helpers")
assert main.resolve_countries(None) == config.ACTIVE_COUNTRIES
assert main.resolve_countries("uk, uae") == ["United Kingdom", "UAE"]
assert main.resolve_countries("all") == list(config.LOCATIONS)
plan = list(main.build_plan(["United Kingdom"], cities=["leeds"], types=["Mini Mart"]))
assert plan == [("United Kingdom", "Leeds", "Mini Mart")]
assert main.week_key(__import__("datetime").date(2026, 10, 14)) == "2026-W42"

# ---------------------------------------------------------------- 13. the sheets helper (only if gspread is installed)
print("13. sheets.append_leads")
try:
    from sheets import append_leads
except ImportError:
    print("   (gspread not installed here, skipped)")
else:
    ws = FakeSheet()
    n = append_leads(ws, [{"Lead ID": "LD-0001", "Business Name": "X", "Lead Priority": "High"}])
    assert n == 1 and ws.rows[1][0] == "LD-0001" and len(ws.rows[1]) == len(SHEET_COLUMNS)

print("\nChunk 9 offline check passed.")