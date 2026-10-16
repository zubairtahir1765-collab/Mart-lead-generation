"""Chunk 8 live check: search -> details -> clean -> website enrichment -> detection -> priority scoring.

Usage:
    python test_priority.py
    python test_priority.py "Dubai" "Supermarket" "UAE" 40

Needs your Places API key (.env) and normal internet access.
Success = "Chunk 8 complete": every lead has High, Medium or Low.
"""
import sys

from cleaning import clean_leads
from detection import detect_leads
from details import enrich_results
from scoring import priority_report, score_leads
from search import search_leads
from website import enrich_leads, save_enriched

if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Leeds"
    business_type = sys.argv[2] if len(sys.argv) > 2 else "Grocery Store"
    country = sys.argv[3] if len(sys.argv) > 3 else "United Kingdom"
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 30

    raw = search_leads(city, business_type, country, max_pages=1)
    leads, _, _ = clean_leads(enrich_results(raw, limit=limit))
    print(f"\nVisiting websites for {len(leads)} leads...")
    enrich_leads(leads)
    detect_leads(leads)
    score_leads(leads)

    report = priority_report(leads)
    print("\nReport:")
    print(f"  total leads  {report['total_leads']}")
    for level, n in report["counts"].items():
        print(f"  {level:<7} {n:>3}")
    print(f"  Call Status 'Not Called': {report['not_called']}   Follow-up Date filled: {report['follow_up_filled']}")

    print("\nSample:")
    for l in sorted(leads, key=lambda x: ("High", "Medium", "Low").index(x.lead_priority))[:10]:
        print(f"  {l.lead_id} {l.business_name[:28]:<28} | {l.lead_priority:<6} | {l.automation_status:<14} | "
              f"{l.google_rating} ({l.reviews_count})")

    path = save_enriched(leads, f"{city}_{business_type}_scored")
    print(f"\nSaved: {path}")
    for p in report["problems"]:
        print(" -", p)
    if report["complete"]:
        print(f"\nChunk 8 complete: all {report['total_leads']} leads have High, Medium or Low.")
    else:
        print("\nFix the problems above.")
