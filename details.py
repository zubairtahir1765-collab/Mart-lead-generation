"""Chunk 4: fetch Place Details and map everything into Lead objects."""
import json
import os
import time
from datetime import datetime

import requests

import guard
from config import (
    DETAILS_CACHE_DIR,
    DETAILS_DELAY_SECONDS,
    GOOGLE_PLACES_API_KEY,
    LEADS_DATA_DIR,
    PLACE_DETAILS_URL,
    SKIP_STATUSES,
)
from models import Lead

DETAILS_FIELD_MASK = ",".join(
    [
        "id",
        "displayName",
        "formattedAddress",
        "internationalPhoneNumber",
        "nationalPhoneNumber",
        "websiteUri",
        "rating",
        "userRatingCount",
        "googleMapsUri",
        "businessStatus",
    ]
)


def _cache_path(place_id: str) -> str:
    return os.path.join(DETAILS_CACHE_DIR, f"{place_id}.json")


def get_place_details(place_id: str, use_cache: bool = True, retries: int = 3) -> dict:
    """Fetch details for one place. Cached on disk so repeat runs cost nothing."""
    path = _cache_path(place_id)
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    guard.charge("details")   # Chunk 10: only real (uncached) requests are counted and limited
    url = PLACE_DETAILS_URL.format(place_id=place_id)
    headers = {
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": DETAILS_FIELD_MASK,
    }
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=20)
        except requests.RequestException as e:
            last_error = str(e)
        else:
            if response.status_code == 200:
                guard.ok()
                data = response.json()
                os.makedirs(DETAILS_CACHE_DIR, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                return data
            last_error = f"{response.status_code}: {response.text}"
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(2 * attempt)
    guard.failed(last_error)
    raise RuntimeError(f"Place details failed for {place_id}: {last_error}")


def build_lead(raw: dict, details: dict) -> Lead:
    """Merge a search result (Chunk 3) with its place details into one Lead."""
    return Lead(
        place_id=raw.get("place_id", ""),
        business_type=raw.get("search_business_type", ""),
        country=raw.get("search_country", ""),
        city=raw.get("search_city", ""),
        business_name=details.get("displayName", {}).get("text") or raw.get("name", ""),
        address=details.get("formattedAddress") or raw.get("address", ""),
        phone_number=details.get("internationalPhoneNumber", ""),
        phone_national=details.get("nationalPhoneNumber", ""),
        website_url=details.get("websiteUri", ""),
        google_rating=details.get("rating", raw.get("rating")),
        reviews_count=details.get("userRatingCount", raw.get("reviews_count")),
        google_maps_url=details.get("googleMapsUri", ""),
        business_status=details.get("businessStatus") or raw.get("business_status", ""),
    )


def enrich_results(raw_results: list[dict], limit: int | None = None) -> list[Lead]:
    """Fetch details for each raw result and return Lead objects."""
    leads = []
    items = raw_results[:limit] if limit else raw_results
    for i, raw in enumerate(items, start=1):
        pid = raw.get("place_id")
        if not pid:
            continue
        if raw.get("business_status") in SKIP_STATUSES:
            print(f"  [{i}/{len(items)}] skipped (closed): {raw.get('name')}")
            continue
        try:
            details = get_place_details(pid)
        except RuntimeError as e:
            print(f"  [{i}/{len(items)}] FAILED: {raw.get('name')} -> {e}")
            continue
        lead = build_lead(raw, details)
        if lead.business_status in SKIP_STATUSES:
            print(f"  [{i}/{len(items)}] skipped (closed): {lead.business_name}")
            continue
        leads.append(lead)
        print(f"  [{i}/{len(items)}] {lead.business_name}")
        time.sleep(DETAILS_DELAY_SECONDS)
    return leads


def completeness_report(leads: list[Lead]) -> dict:
    """How many leads have each Google-supplied field."""
    fields = {
        "business_name": lambda l: l.business_name,
        "address": lambda l: l.address,
        "phone_number": lambda l: l.phone_number,
        "website_url": lambda l: l.website_url,
        "google_rating": lambda l: l.google_rating is not None,
        "reviews_count": lambda l: l.reviews_count is not None,
        "google_maps_url": lambda l: l.google_maps_url,
    }
    total = len(leads) or 1
    return {name: sum(1 for l in leads if getter(l)) for name, getter in fields.items()} | {"total": len(leads), "_denominator": total}


def save_leads(leads: list[Lead], label: str) -> str:
    os.makedirs(LEADS_DATA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LEADS_DATA_DIR, f"{label.lower().replace(' ', '_')}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
    return path
