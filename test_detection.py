"""Chunk 7 live check: search -> details -> clean -> website enrichment -> order/delivery/automation detection.

Usage:
    python test_detection.py
    python test_detection.py "Dubai" "Supermarket" "UAE" 40

Needs your Places API key (.env) and normal internet access.
Success = "Chunk 7 complete": every lead has Order Method, Delivery System and
Automation Status filled (Unknown is allowed as the fallback).
"""
import sys

from cleaning import clean_leads
from detection import detect_leads, detection_report
from details import enrich_results
from search import search_leads
from website import enrich_leads, enrichment_report, save_enriched

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

    report = detection_report(leads)
    print("\nReport:")
    print(f"  total leads        {report['total_leads']}")
    for key in ("order_method", "delivery_system", "automation_status"):
        print(f"  {key}:")
        for value, n in report[key].items():
            print(f"      {n:>3}  {value}")
    print(f"  Unknown: order {report['order_unknown']}, delivery {report['delivery_unknown']}, "
          f"automation {report['automation_unknown']}")

    print("\nSample:")
    for l in leads[:8]:
        print(f"  {l.lead_id} {l.business_name[:28]:<28} | {l.order_method[:34]:<34} | "
              f"{l.delivery_system:<20} | {l.automation_status:<14} | {l.detection_note[:70]}")

    path = save_enriched(leads, f"{city}_{business_type}_detected")
    print(f"\nSaved: {path}")

    for p in report["problems"]:
        print(" -", p)
    if report["complete"]:
        print(f"\nChunk 7 complete: all {report['total_leads']} leads have Order Method, Delivery System "
              "and Automation Status filled.")
        site = enrichment_report(leads)
        if site["site_unreachable"] or site["site_blocked"]:
            print(f"({site['site_unreachable']} unreachable and {site['site_blocked']} blocked sites were set to Unknown.)")
    else:
        print("\nFix the problems above.")
