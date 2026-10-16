"""Chunk 5: clean, verify phones, de-duplicate and assign Lead IDs."""
import json
import os
import re
from collections import Counter
from datetime import datetime

import phonenumbers

from config import CLEANED_DATA_DIR, COUNTRY_PHONE_REGIONS, NAME_NOISE_WORDS
from config import COUNTRY_REGION_CODES
from models import Lead


# ---------------------------------------------------------------- phones
def normalize_phone(raw: str, country: str) -> tuple[str | None, str | None, str]:
    """
    Verify a phone number.
    Returns (e164, international_format, reason). On failure e164 is None and
    reason says why: 'missing', 'unparseable', 'invalid', 'wrong_country'.
    """
    if not raw or not raw.strip():
        return None, None, "missing"

    default_region = COUNTRY_REGION_CODES.get(country)
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None, None, "unparseable"

    if not phonenumbers.is_valid_number(parsed):
        return None, None, "invalid"

    allowed = COUNTRY_PHONE_REGIONS.get(country)
    if allowed and phonenumbers.region_code_for_number(parsed) not in allowed:
        return None, None, "wrong_country"

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    intl = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    return e164, intl, "ok"


# ---------------------------------------------------------------- text
def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def name_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in NAME_NOISE_WORDS)


def address_key(address: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (address or "").lower())


def _score(lead: Lead) -> int:
    """Used to keep the most complete record when two leads are duplicates."""
    return sum(
        [
            bool(lead.website_url),
            bool(lead.phone_e164),
            lead.google_rating is not None,
            bool(lead.google_maps_url),
        ]
    ) * 1_000_000 + (lead.reviews_count or 0)


# ---------------------------------------------------------------- main pipeline
def clean_leads(leads: list[Lead], start_id: int = 0,
                existing_phones: set[str] | None = None,
                existing_name_addr: set[str] | None = None):
    """
    Returns (clean_leads, rejected, stats).

    - normalises text and phone numbers (valid format + correct country)
    - drops leads with no phone / invalid phone / wrong-country phone
    - removes duplicates by place_id, phone, or name + address
    - skips leads already in the sheet (pass existing_phones / existing_name_addr)
    - assigns unique Lead IDs continuing from start_id
    """
    existing_phones = existing_phones or set()
    existing_name_addr = existing_name_addr or set()
    rejected: list[dict] = []
    reasons = Counter()

    # 1) normalise + verify phones
    verified: list[Lead] = []
    for lead in leads:
        lead.business_name = clean_text(lead.business_name)
        lead.address = clean_text(lead.address)

        raw_phone = lead.phone_number or lead.phone_national
        e164, intl, reason = normalize_phone(raw_phone, lead.country)
        if reason != "ok":
            reasons[f"phone_{reason}"] += 1
            rejected.append({"reason": f"phone_{reason}", "name": lead.business_name,
                             "phone": raw_phone, "address": lead.address})
            continue
        lead.phone_e164, lead.phone_number = e164, intl
        verified.append(lead)

    # 2) de-duplicate (best record wins)
    best: dict[str, Lead] = {}
    order: list[str] = []
    seen_phone: dict[str, str] = {}
    seen_na: dict[str, str] = {}
    seen_pid: dict[str, str] = {}

    for lead in verified:
        na = f"{name_key(lead.business_name)}|{address_key(lead.address)}"
        if lead.phone_e164 in existing_phones or na in existing_name_addr:
            reasons["already_in_sheet"] += 1
            rejected.append({"reason": "already_in_sheet", "name": lead.business_name,
                             "phone": lead.phone_number, "address": lead.address})
            continue

        match = (seen_pid.get(lead.place_id) if lead.place_id else None) \
            or seen_phone.get(lead.phone_e164) or seen_na.get(na)

        if match is None:
            key = lead.phone_e164
            best[key] = lead
            order.append(key)
            seen_phone[lead.phone_e164] = key
            seen_na[na] = key
            if lead.place_id:
                seen_pid[lead.place_id] = key
        else:
            reasons["duplicate"] += 1
            kept = best[match]
            loser = lead
            if _score(lead) > _score(kept):
                best[match], loser = lead, kept
            rejected.append({"reason": "duplicate", "name": loser.business_name,
                             "phone": loser.phone_number, "address": loser.address})
            # keep lookup tables pointing at the surviving record
            seen_na[na] = match
            if lead.place_id:
                seen_pid[lead.place_id] = match

    unique = [best[k] for k in order]

    # 3) assign Lead IDs
    for i, lead in enumerate(unique, start=start_id + 1):
        lead.lead_id = f"LD-{i:04d}"

    stats = {
        "input": len(leads),
        "output": len(unique),
        "dropped_total": len(leads) - len(unique),
        **dict(reasons),
    }
    return unique, rejected, stats


# ---------------------------------------------------------------- checks + saving
def validate_output(leads: list[Lead]) -> list[str]:
    """Return a list of problems. Empty list = Chunk 5 done."""
    problems = []
    phones = Counter(l.phone_e164 for l in leads)
    problems += [f"duplicate phone: {p}" for p, c in phones.items() if c > 1]
    ids = Counter(l.lead_id for l in leads)
    problems += [f"duplicate lead id: {i}" for i, c in ids.items() if c > 1]
    na = Counter(f"{name_key(l.business_name)}|{address_key(l.address)}" for l in leads)
    problems += [f"duplicate name+address: {k}" for k, c in na.items() if c > 1]
    for l in leads:
        e164, _, reason = normalize_phone(l.phone_e164, l.country)
        if reason != "ok":
            problems.append(f"invalid phone ({reason}): {l.business_name} {l.phone_e164}")
    return problems


def save_cleaned(leads: list[Lead], rejected: list[dict], label: str) -> tuple[str, str]:
    os.makedirs(CLEANED_DATA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = label.lower().replace(" ", "_")
    clean_path = os.path.join(CLEANED_DATA_DIR, f"{base}_{stamp}.json")
    rej_path = os.path.join(CLEANED_DATA_DIR, f"{base}_{stamp}_rejected.json")
    with open(clean_path, "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
    with open(rej_path, "w", encoding="utf-8") as f:
        json.dump(rejected, f, indent=2, ensure_ascii=False)
    return clean_path, rej_path
