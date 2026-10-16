"""Chunk 9: the pipeline. One command fills the sheet with this week's new, clean leads.

    python main.py                       # active countries, 100 leads for the week
    python main.py --dry-run             # everything except writing to the sheet
    python main.py --target 150
    python main.py --countries "UAE,Pakistan"      # or: --countries all
    python main.py --cities "Leeds,London" --types "Supermarket,Mini Mart"

What one run does (countries -> cities -> business types, in the order of config.py):
  1. read the sheet, so leads already there from earlier weeks are skipped (by phone, or name + address)
  2. search -> place details -> clean (verified phone, duplicates out)   [Chunks 3-5]
  3. website enrichment -> order / delivery / automation -> priority      [Chunks 6-8]
  4. append the new leads to the sheet right away (a crash never loses a finished batch)
  5. stop as soon as the week's target is reached

Exit codes: 0 target reached, 1 short of the target, 2 stopped on an error, 3 stopped by the API quota limit (Chunk 10).

The week's running total and every place already processed are kept in data/pipeline_state.json, so running
the command twice in a week only fetches what is still missing, and details of known places are never paid for twice.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date

from cleaning import address_key, clean_leads, name_key, normalize_phone, validate_output
from config import (
    ACTIVE_COUNTRIES, BUSINESS_TYPES, COUNTRY_QUERY_NAMES, GOOGLE_SHEET_ID, LOCATIONS,
    PIPELINE_MAX_DETAILS_PER_QUERY, PIPELINE_MAX_PER_CITY, PIPELINE_MAX_PER_QUERY, PIPELINE_MAX_QUERIES,
    PIPELINE_QUERY_DELAY_SECONDS, PIPELINE_STATE_FILE, SHEET_COLUMNS, WEEKLY_LEAD_TARGET,
)
from detection import detect_leads, validate_detection
from details import enrich_results
from guard import GuardStop
from scoring import priority_report, score_leads, validate_priority
from search import search_leads
from website import enrich_leads, save_enriched


class PipelineError(RuntimeError):
    """Something that must stop the run (bad sheet, failed write, failed validation)."""


# ---------------------------------------------------------------- helpers
def week_key(today: date | None = None) -> str:
    year, week, _ = (today or date.today()).isocalendar()
    return f"{year}-W{week:02d}"


def resolve_countries(arg: str | None) -> list[str]:
    if not arg:
        return list(ACTIVE_COUNTRIES)
    if arg.strip().lower() == "all":
        return list(LOCATIONS)
    lookup = {c.lower(): c for c in LOCATIONS}
    lookup.update({v.lower(): k for k, v in COUNTRY_QUERY_NAMES.items()})   # "UK" -> United Kingdom
    out = []
    for part in arg.split(","):
        key = part.strip().lower()
        if key not in lookup:
            raise SystemExit(f"Unknown country '{part.strip()}'. Choose from: {', '.join(LOCATIONS)} or 'all'.")
        if lookup[key] not in out:
            out.append(lookup[key])
    return out


def build_plan(countries, cities=None, types=None):
    """countries -> cities -> business types, as (country, city, business_type)."""
    cities = {c.strip().lower() for c in cities} if cities else None
    for country in countries:
        for city in LOCATIONS[country]:
            if cities and city.lower() not in cities:
                continue
            for business_type in (types or BUSINESS_TYPES):
                yield country, city, business_type


# ---------------------------------------------------------------- weekly state
def load_state(path: str, week: str) -> tuple[int, set[str]]:
    """(leads added so far this week, place IDs already processed)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    added = int(data.get("added", 0)) if data.get("week") == week else 0
    return added, set(data.get("seen_place_ids", []))


def save_state(path: str, week: str, added: int, seen: set[str]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"week": week, "added": added, "seen_place_ids": sorted(seen)}, f)


# ---------------------------------------------------------------- what is already in the sheet
def _sheet_phone_e164(raw: str, country: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    e164 = normalize_phone(raw, country)[0]
    if not e164 and raw.isdigit():            # Sheets can drop the "+" from a number-looking cell
        e164 = normalize_phone("+" + raw, country)[0]
    return e164


def read_sheet_keys(values: list[list[str]]) -> tuple[set[str], set[str], int]:
    """From the sheet's rows: (phones as E.164, 'name|address' keys, highest Lead ID number)."""
    phones, name_addr, max_id = set(), set(), 0
    if not values:
        return phones, name_addr, max_id
    idx = {h: i for i, h in enumerate(values[0])}

    def cell(row, col):
        i = idx.get(col)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in values[1:]:
        lead_id = cell(row, "Lead ID")
        if lead_id.startswith("LD-") and lead_id[3:].isdigit():
            max_id = max(max_id, int(lead_id[3:]))
        e164 = _sheet_phone_e164(cell(row, "Phone Number"), cell(row, "Country"))
        if e164:
            phones.add(e164)
        name, addr = cell(row, "Business Name"), cell(row, "Address")
        if name or addr:
            name_addr.add(f"{name_key(name)}|{address_key(addr)}")
    return phones, name_addr, max_id


def _append_with_retry(append_fn, ws, rows, retries=3, sleep_fn=time.sleep):
    last = None
    for attempt in range(1, retries + 1):
        try:
            return append_fn(ws, rows)
        except Exception as e:                 # rate limits and network blips
            last = e
            sleep_fn(2 * attempt)
    raise PipelineError(f"Could not write to the sheet after {retries} tries: {last}")


# ---------------------------------------------------------------- the pipeline
def run_pipeline(ws, target=WEEKLY_LEAD_TARGET, countries=None, *, cities=None, types=None, dry_run=False,
                 state_path=PIPELINE_STATE_FILE, today=None,
                 max_per_query=PIPELINE_MAX_PER_QUERY, max_per_city=PIPELINE_MAX_PER_CITY,
                 max_details=PIPELINE_MAX_DETAILS_PER_QUERY, max_queries=PIPELINE_MAX_QUERIES,
                 search_fn=search_leads, details_fn=enrich_results, clean_fn=clean_leads,
                 enrich_fn=enrich_leads, append_fn=None, sleep_fn=time.sleep, log=print) -> dict:
    """Fill the sheet with new leads until `target` is reached for the week. Returns a summary dict."""
    countries = countries or list(ACTIVE_COUNTRIES)
    week = week_key(today)
    week_added, seen = load_state(state_path, week)
    summary = {"week": week, "target": target, "added": 0, "week_total": week_added, "queries": 0,
               "shortfall": 0, "leads": [], "dry_run": dry_run, "stop_reason": None, "stop_kind": None}

    need = target - week_added
    if need <= 0:
        log(f"Week {week}: already {week_added} leads added (target {target}). Nothing to do. "
            "Use --target to ask for more.")
        return summary

    # 1) what is already in the sheet
    values = ws.get_all_values() if ws is not None else []
    if ws is not None and (not values or values[0] != SHEET_COLUMNS):
        raise PipelineError("The sheet's header row is missing or different. Run: python setup_sheet.py")
    phones, name_addr, next_id = read_sheet_keys(values)
    log(f"Week {week}: target {target}, already added {week_added}, need {need} more. "
        f"Sheet has {max(len(values) - 1, 0)} leads ({len(phones)} verified phones) to skip.")

    if append_fn is None and not dry_run:
        from sheets import append_leads
        append_fn = append_leads

    plan = list(build_plan(countries, cities, types))
    city_counts: Counter = Counter()
    passes = [(max_per_query, max_per_city), (None, None)]   # second pass relaxes the caps
    interrupted = False
    stop: GuardStop | None = None

    try:
        for pass_no, (q_cap, c_cap) in enumerate(passes, start=1):
            if need <= 0 or summary["queries"] >= max_queries:
                break
            if pass_no == 2:
                log(f"\nStill {need} short with the per-city/per-query caps. Second pass without caps...")
            for country, city, btype in plan:
                if need <= 0:
                    break
                if summary["queries"] >= max_queries:
                    log(f"Search limit reached ({max_queries} per run, PIPELINE_MAX_QUERIES). Stopping.")
                    break
                room = need
                if q_cap:
                    room = min(room, q_cap)
                if c_cap:
                    room = min(room, c_cap - city_counts[(country, city)])
                if room <= 0:
                    continue

                summary["queries"] += 1
                log(f"\n[{summary['queries']}] {btype} in {city}, {country} (need {need} more)")
                try:
                    raw = search_fn(city, btype, country)
                except RuntimeError as e:
                    log(f"  search failed, skipping: {e}")
                    continue
                raw = [r for r in raw if r.get("place_id") and r["place_id"] not in seen]
                if not raw:
                    log("  nothing new in these results")
                    continue

                leads = details_fn(raw, limit=max_details)
                if not leads:
                    continue
                clean, _rejected, stats = clean_fn(leads, 0, phones, name_addr)
                log(f"  {stats['input']} checked -> {stats['output']} new verified "
                    f"(dropped: {stats['dropped_total']})")

                clean.sort(key=lambda l: l.reviews_count or 0, reverse=True)   # keep the best-reviewed first
                batch, trimmed = clean[:room], clean[room:]
                trimmed_ids = {l.place_id for l in trimmed}
                processed_ids = {l.place_id for l in leads if l.place_id} - trimmed_ids

                if batch:
                    enrich_fn(batch)
                    detect_leads(batch)
                    score_leads(batch)
                    for i, lead in enumerate(batch, start=next_id + 1):
                        lead.lead_id = f"LD-{i:04d}"

                    problems = validate_output(batch) + validate_detection(batch) + validate_priority(batch)
                    if problems:
                        raise PipelineError("Validation failed, nothing written for this batch:\n  - "
                                            + "\n  - ".join(problems[:10]))

                    backup = save_enriched(batch, f"{city}_{btype}_week")
                    if not dry_run:
                        try:
                            _append_with_retry(append_fn, ws, [l.to_sheet_dict() for l in batch],
                                               sleep_fn=sleep_fn)
                        except PipelineError as e:
                            raise PipelineError(f"{e}\nThis batch is saved in {backup}") from e

                    next_id += len(batch)
                    need -= len(batch)
                    week_added += len(batch)
                    city_counts[(country, city)] += len(batch)
                    for lead in batch:
                        phones.add(lead.phone_e164)
                        name_addr.add(f"{name_key(lead.business_name)}|{address_key(lead.address)}")
                    summary["leads"].extend(batch)
                    log(f"  {'would add' if dry_run else 'added'} {len(batch)} leads "
                        f"(this run {len(summary['leads'])}, week total {week_added})")

                seen |= processed_ids
                if not dry_run:
                    save_state(state_path, week, week_added, seen)
                if need > 0:
                    sleep_fn(PIPELINE_QUERY_DELAY_SECONDS)
    except KeyboardInterrupt:
        interrupted = True
        log("\nInterrupted. Batches already written to the sheet are kept.")
    except GuardStop as e:     # Chunk 10: API quota reached, or Google keeps failing. Everything already written is kept.
        stop = e
        log(f"\nSTOPPED EARLY: {e}\nBatches already written to the sheet are kept.")

    summary.update(added=len(summary["leads"]), week_total=week_added, shortfall=max(need, 0),
                   interrupted=interrupted, stop_reason=str(stop) if stop else None,
                   stop_kind=stop.stop_kind if stop else None)
    _print_summary(summary, log)
    return summary


def _print_summary(s: dict, log) -> None:
    leads = s["leads"]
    verb = "would be added (dry run)" if s["dry_run"] else "added to the sheet"
    log("\n" + "=" * 60)
    log(f"{s['added']} new leads {verb}. Week {s['week']} total: {s['week_total']} / {s['target']}. "
        f"Searches used: {s['queries']}.")
    if leads:
        by_city = Counter(f"{l.city}, {l.country}" for l in leads)
        log("By city:     " + "; ".join(f"{k} {v}" for k, v in by_city.most_common()))
        log("By priority: " + ", ".join(f"{k} {v}" for k, v in priority_report(leads)["counts"].items()))
    if s.get("stop_reason"):
        log(f"Short by {s['shortfall']} because the run was stopped early (see above). Run it again once that is fixed: "
            "places already processed this week are remembered, so nothing is paid for twice.")
    elif s["shortfall"]:
        log(f"Short by {s['shortfall']}. Every search for the selected countries is used up, or the search limit was "
            "hit. Add more countries (--countries all), or raise PIPELINE_MAX_QUERIES / "
            "PIPELINE_MAX_DETAILS_PER_QUERY in config.py.")
    elif not s["dry_run"]:
        log(f"Pipeline complete: the sheet has {s['week_total']} leads this week (target {s['target']}).")


# ---------------------------------------------------------------- command line
def build_parser() -> argparse.ArgumentParser:
    """The command-line options. run_weekly.py reuses this, so both entry points accept the same arguments."""
    p = argparse.ArgumentParser(description="Fill the Google Sheet with this week's new mart leads.")
    p.add_argument("--target", type=int, default=WEEKLY_LEAD_TARGET, help="leads wanted this week (default 100)")
    p.add_argument("--countries", help="comma list, or 'all' (default: ACTIVE_COUNTRIES in config.py)")
    p.add_argument("--cities", help="only these cities, comma list")
    p.add_argument("--types", help="only these business types, comma list")
    p.add_argument("--dry-run", action="store_true", help="do everything except write to the sheet or save progress")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    countries = resolve_countries(args.countries)
    types = [t.strip() for t in args.types.split(",")] if args.types else None
    cities = [c.strip() for c in args.cities.split(",")] if args.cities else None

    ws = None
    if GOOGLE_SHEET_ID and GOOGLE_SHEET_ID != "your_sheet_id_here":
        from sheets import get_worksheet
        ws = get_worksheet()
    elif not args.dry_run:
        raise SystemExit("ERROR: GOOGLE_SHEET_ID is missing in .env (see Chunk 2 in the README).")
    else:
        print("No GOOGLE_SHEET_ID set: dry run without the sheet, so nothing is skipped as 'already in the sheet'.")

    try:
        result = run_pipeline(ws, args.target, countries, cities=cities, types=types, dry_run=args.dry_run)
    except PipelineError as e:
        print(f"\nSTOPPED: {e}")
        return 2
    if result.get("stop_kind") == "quota":
        return 3
    if result.get("stop_kind") == "circuit":
        return 2
    return 1 if result["shortfall"] else 0


if __name__ == "__main__":
    sys.exit(main())
