import sys
import requests
from config import GOOGLE_PLACES_API_KEY, PLACES_SEARCH_URL


def test_search():
    if not GOOGLE_PLACES_API_KEY or GOOGLE_PLACES_API_KEY == "your_api_key_here":
        print("ERROR: GOOGLE_PLACES_API_KEY is missing. Copy .env.example to .env and add your key.")
        sys.exit(1)

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.rating,places.userRatingCount"
        ),
    }
    body = {"textQuery": "Asian grocery store in Leeds, UK", "pageSize": 5}

    response = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=15)
    print("Status:", response.status_code)

    if response.status_code != 200:
        print(response.text)
        print("\nHints: enable 'Places API (New)', enable billing, check your key restrictions.")
        sys.exit(1)

    places = response.json().get("places", [])
    print(f"Found {len(places)} places\n")
    for p in places:
        print(
            p["displayName"]["text"], "|", p.get("formattedAddress"),
            "|", p.get("rating"), f"({p.get('userRatingCount')} reviews)",
        )
    print("\nChunk 1 complete: Places API is working.")


if __name__ == "__main__":
    test_search()
