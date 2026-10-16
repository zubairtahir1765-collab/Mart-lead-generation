"""Chunk 5 live check: search -> details -> clean -> validate.

Usage:
    python test_cleaning.py
    python test_cleaning.py "Leeds" "Grocery Store" "United Kingdom" 20
"""
import sys

from cleaning import clean_leads, save_cleaned, validate_output
from details import enrich_results
from search import search_leads

if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Leeds"
    business_type = sys.argv[2] if len(sys.argv) > 2 else "Grocery Store"
    country = sys.argv[3] if len(sys.argv) > 3 else "United Kingdom"
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 20

    raw = search_leads(city, business_type, country, max_pages=1)
    leads = enrich_results(raw, limit=limit)

    clean, rejected, stats = clean_leads(leads)
    print("\nStats:", stats)

    problems = validate_output(clean)
    clean_path, rej_path = save_cleaned(clean, rejected, f"{city}_{business_type}")
    print(f"Saved: {clean_path}\nRejected log: {rej_path}")

    if problems:
        print("\nProblems found:")
        for p in problems:
            print(" -", p)
    else:
        print(f"\nChunk 5 complete: {len(clean)} leads, no duplicates, no invalid phones.")
