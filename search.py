"""Chunk 3: Lead search using Google Places Text Search (New) with pagination."""
import json
import os
import time
from datetime import datetime

import requests

import guard
from config import (
    COUNTRY_QUERY_NAMES,
    COUNTRY_REGION_CODES,
    GOOGLE_PLACES_API_KEY,
    PLACES_SEARCH_URL,
    RAW_DATA_DIR,
    SEARCH_MAX_PAGES,
    SEARCH_PAGE_DELAY_SECONDS,
    SEARCH_PAGE_SIZE,
)

# Only request what we need: the field mask controls cost.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
        "places.primaryType",
        "places.businessStatus",
        "places.rating",
        "places.userRatingCount",
        "nextPageToken",  # required for pagination
    ]
)


def build_query(business_type: str, city: str, country: str) -> str:
    """e.g. 'Asian Grocery Store in Leeds, UK'"""
    country_name = COUNTRY_QUERY_NAMES.get(country, country)
    return f"{business_type} in {city}, {country_name}"


def _post_with_retry(body: dict, retries: int = 3) -> dict:
    guard.charge("search")   # Chunk 10: quota check before every billed request (raises guard.QuotaExceeded)
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=20)
        except requests.RequestException as e:
            last_error = str(e)
        else:
            if response.status_code == 200:
                guard.ok()
                return response.json()
            last_error = f"{response.status_code}: {response.text}"
            # Retry only on rate limit / server errors
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(2 * attempt)
    guard.failed(last_error)   # Chunk 10: many failures in a row stop the whole run (raises guard.ApiCircuitOpen)
    raise RuntimeError(f"Places search failed: {last_error}")


def _normalize(place: dict, city: str, country: str, business_type: str, query: str) -> dict:
    return {
        "place_id": place.get("id"),
        "name": place.get("displayName", {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "lat": place.get("location", {}).get("latitude"),
        "lng": place.get("location", {}).get("longitude"),
        "types": place.get("types", []),
        "primary_type": place.get("primaryType", ""),
        "business_status": place.get("businessStatus", ""),
        "rating": place.get("rating"),
        "reviews_count": place.get("userRatingCount"),
        # search context (useful in later chunks)
        "search_city": city,
        "search_country": country,
        "search_business_type": business_type,
        "search_query": query,
    }


def search_leads(city: str, business_type: str, country: str = "United Kingdom",
                 max_pages: int = SEARCH_MAX_PAGES) -> list[dict]:
    """Return raw, de-duplicated results for one city + business type."""
    if not GOOGLE_PLACES_API_KEY or GOOGLE_PLACES_API_KEY == "your_api_key_here":
        raise SystemExit("ERROR: GOOGLE_PLACES_API_KEY is missing in .env")

    query = build_query(business_type, city, country)
    body = {"textQuery": query, "pageSize": SEARCH_PAGE_SIZE}
    region = COUNTRY_REGION_CODES.get(country)
    if region:
        body["regionCode"] = region

    results, seen = [], set()
    for page in range(1, max_pages + 1):
        data = _post_with_retry(body)
        for place in data.get("places", []):
            pid = place.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                results.append(_normalize(place, city, country, business_type, query))

        token = data.get("nextPageToken")
        print(f"  page {page}: {len(results)} total so far")
        if not token:
            break
        body["pageToken"] = token
        time.sleep(SEARCH_PAGE_DELAY_SECONDS)

    return results


def save_raw(results: list[dict], city: str, business_type: str) -> str:
    """Save raw results to data/raw/ as JSON so you can inspect them."""
    os.makedirs(RAW_DATA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = f"{city}_{business_type}".lower().replace(" ", "_")
    path = os.path.join(RAW_DATA_DIR, f"{safe}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    return path
