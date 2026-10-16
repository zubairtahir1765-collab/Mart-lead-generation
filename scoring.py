"""Chunk 8: lead priority scoring (High / Medium / Low). Rule-based, no API calls.

Rules (thresholds live in the Chunk 8 block of config.py), checked in this order:
  1. Low    - automation is "Automated" (already has an online store, app, chatbot or CRM),
              or there is no phone number (nobody to call).
  2. High   - automation is Manual or Semi Automated
              AND rating >= PRIORITY_HIGH_MIN_RATING
              AND reviews >= PRIORITY_HIGH_MIN_REVIEWS
              AND has a phone number.
  3. Medium - everything else: automation Unknown, rating or reviews missing / not high enough.

A short reason is stored on the lead (priority_note) and shown in Notes, e.g.
  "Priority High: Manual, 4.6 stars, 212 reviews, phone ok"
Call Status is set to "Not Called" when blank; Follow-up Date is left alone (blank for new leads).
"""
from config import (
    PRIORITY_HIGH, PRIORITY_HIGH_AUTOMATION, PRIORITY_HIGH_MIN_RATING, PRIORITY_HIGH_MIN_REVIEWS,
    PRIORITY_LOW, PRIORITY_LOW_AUTOMATION, PRIORITY_MEDIUM, PRIORITY_VALUES,
)
from models import Lead


def _has_phone(lead: Lead) -> bool:
    return bool((lead.phone_e164 or lead.phone_number or "").strip())


def _as_number(value, cast):
    try:
        return cast(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def score_lead(lead: Lead) -> tuple[str, str]:
    """Return (priority, short reason) for one lead."""
    auto = (lead.automation_status or "").strip()
    rating = _as_number(lead.google_rating, float)
    reviews = _as_number(lead.reviews_count, int)
    phone = _has_phone(lead)

    facts = [auto or "automation not checked"]
    facts.append(f"{rating:g} stars" if rating is not None else "no rating")
    facts.append(f"{reviews} reviews" if reviews is not None else "no review count")
    facts.append("phone ok" if phone else "no phone")
    summary = ", ".join(facts)

    if auto in PRIORITY_LOW_AUTOMATION:
        return PRIORITY_LOW, f"Priority Low: already {auto} ({summary})"
    if not phone:
        return PRIORITY_LOW, f"Priority Low: no phone number to call ({summary})"

    good_rating = rating is not None and rating >= PRIORITY_HIGH_MIN_RATING
    many_reviews = reviews is not None and reviews >= PRIORITY_HIGH_MIN_REVIEWS
    if auto in PRIORITY_HIGH_AUTOMATION and good_rating and many_reviews:
        return PRIORITY_HIGH, f"Priority High: {summary}"

    # Medium: say what kept it from High
    gaps = []
    if auto not in PRIORITY_HIGH_AUTOMATION:
        gaps.append("automation unclear")
    if not good_rating:
        gaps.append(f"rating under {PRIORITY_HIGH_MIN_RATING:g}" if rating is not None else "no rating")
    if not many_reviews:
        gaps.append(f"under {PRIORITY_HIGH_MIN_REVIEWS} reviews" if reviews is not None else "no review count")
    return PRIORITY_MEDIUM, f"Priority Medium: {summary}; not High because {', '.join(gaps)}"


def score_leads(leads: list[Lead]) -> list[Lead]:
    """Set lead_priority, priority_note and call_status on every lead. Never leaves priority blank."""
    for lead in leads:
        try:
            lead.lead_priority, lead.priority_note = score_lead(lead)
        except Exception as e:  # a bad record must not stop the batch
            lead.lead_priority, lead.priority_note = PRIORITY_MEDIUM, f"Priority Medium: scoring error ({e})"
        if not lead.call_status:
            lead.call_status = "Not Called"
    return leads


def validate_priority(leads: list[Lead]) -> list[str]:
    """Problems found; an empty list means Chunk 8 is done."""
    problems = []
    for lead in leads:
        who = lead.lead_id or lead.business_name or "(unnamed lead)"
        if lead.lead_priority not in PRIORITY_VALUES:
            problems.append(f"{who}: Lead Priority is '{lead.lead_priority}', expected High/Medium/Low")
        if not lead.call_status:
            problems.append(f"{who}: Call Status is blank")
    return problems


def priority_report(leads: list[Lead]) -> dict:
    counts = {p: 0 for p in PRIORITY_VALUES}
    for lead in leads:
        if lead.lead_priority in counts:
            counts[lead.lead_priority] += 1
    problems = validate_priority(leads)
    return {
        "total_leads": len(leads),
        "counts": counts,
        "not_called": sum(1 for l in leads if l.call_status == "Not Called"),
        "follow_up_filled": sum(1 for l in leads if l.follow_up_date),
        "problems": problems,
        "complete": bool(leads) and not problems,
    }
