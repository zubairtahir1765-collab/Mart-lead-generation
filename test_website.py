"""Chunk 6 live check: search -> details -> clean -> website/social enrichment.

Usage:
    python test_website.py
    python test_website.py "Leeds" "Grocery Store" "United Kingdom" 30

Needs your Places API key (.env) and normal internet access.
Success = the report says "Chunk 6 complete" (at least 30% of leads with a website have
an email, Instagram/Facebook link or owner name).
"""
import sys

from cleaning import clean_leads
from details import enrich_results
from search import search_leads
from website import (enrich_leads, enrichment_report, save_enriched,
                     validate_enrichment)

if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Leeds"
    business_type = sys.argv[2] if len(sys.argv) > 2 else "Grocery Store"
    country = sys.argv[3] if len(sys.argv) > 3 else "United Kingdom"
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 30

    raw = search_leads(city, business_type, country, max_pages=1)
    leads, _, _ = clean_leads(enrich_results(raw, limit=limit))

    print(f"\nVisiting websites for {len(leads)} leads...")
    enrich_leads(leads)

    report = enrichment_report(leads)
    print("\nReport:")
    for key, value in report.items():
        print(f"  {key:<22} {value}")

    print(f"\nSample:")
    for l in [x for x in leads if x.website_url][:8]:
        print(f"  {l.lead_id} {l.business_name[:30]:<30} {l.website_available} | "
              f"{l.email or '-':<28} | {l.social_links or '-'} | {l.owner_name or '-'}")

    path = save_enriched(leads, f"{city}_{business_type}")
    print(f"\nSaved: {path}")

    problems = validate_enrichment(leads)
    for p in problems:
        print(" -", p)

    if problems:
        print("\nFix the problems above.")
    elif report["meets_target"]:
        print(f"\nChunk 6 complete: {report['enriched_pct']}% of leads with a website have extra data "
              f"(target {report['target_pct']}%+).")
    else:
        print(f"\nOnly {report['enriched_pct']}% enriched (target {report['target_pct']}%). "
              "Check site_unreachable / site_blocked above; try a larger sample or another city.")
