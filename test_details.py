"""Chunk 4 check: search -> place details -> Lead objects.

Usage:
    python test_details.py                      # Leeds, Asian Grocery Store, first 10 results
    python test_details.py "Leeds" "Supermarket" "United Kingdom" 20
"""
import sys

from details import completeness_report, enrich_results, save_leads
from search import search_leads

if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Leeds"
    business_type = sys.argv[2] if len(sys.argv) > 2 else "Asian Grocery Store"
    country = sys.argv[3] if len(sys.argv) > 3 else "United Kingdom"
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 10

    print(f"Searching: {business_type} in {city}")
    raw = search_leads(city, business_type, country, max_pages=1)

    print(f"\nFetching details for up to {limit} places...")
    leads = enrich_results(raw, limit=limit)

    report = completeness_report(leads)
    total = report.pop("total")
    report.pop("_denominator")
    print(f"\nCompleteness across {total} leads:")
    for field_name, count in report.items():
        print(f"  {field_name:<16} {count}/{total}")

    if leads:
        sample = leads[0].to_sheet_dict()
        print("\nSample lead (sheet format):")
        for key, value in sample.items():
            print(f"  {key}: {value}")

    path = save_leads(leads, f"{city}_{business_type}")
    print(f"\nSaved leads to {path}")
    print("Chunk 4 complete." if leads else "No leads returned. Check the output above.")
