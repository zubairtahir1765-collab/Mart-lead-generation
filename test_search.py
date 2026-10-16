"""Chunk 3 check: one city + one business type should return 20+ raw results.

Usage:
    python test_search.py
    python test_search.py "Manchester" "Supermarket" "United Kingdom"
"""
import sys

from search import build_query, save_raw, search_leads

if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "Leeds"
    business_type = sys.argv[2] if len(sys.argv) > 2 else "Asian Grocery Store"
    country = sys.argv[3] if len(sys.argv) > 3 else "United Kingdom"

    print(f"Query: {build_query(business_type, city, country)}")
    results = search_leads(city, business_type, country)

    print(f"\nFound {len(results)} unique results\n")
    for r in results[:10]:
        print(f"- {r['name']} | {r['address']} | {r['rating']} ({r['reviews_count']})")
    if len(results) > 10:
        print(f"... and {len(results) - 10} more")

    path = save_raw(results, city, business_type)
    print(f"\nSaved raw results to {path}")

    if len(results) >= 20:
        print("Chunk 3 complete: 20+ results returned.")
    else:
        print("Fewer than 20 results. Try a bigger city or a broader business type.")
