"""Chunk 8 check: lead priority scoring. No internet or API key needed.

Usage:
    python test_scoring.py
Success = "Chunk 8 complete" and every assert passes.
"""
from config import PRIORITY_HIGH_MIN_RATING, PRIORITY_HIGH_MIN_REVIEWS
from models import Lead
from scoring import priority_report, score_lead, score_leads, validate_priority


def mk(auto="Manual", rating=4.5, reviews=200, phone="+441131234567", **kw):
    return Lead(lead_id=kw.pop("lead_id", "LD-0001"), business_name=kw.pop("name", "Test Mart"),
                automation_status=auto, google_rating=rating, reviews_count=reviews,
                phone_number=phone, phone_e164=phone, **kw)


def pri(**kw):
    return score_lead(mk(**kw))[0]


print("High")
assert pri() == "High"                                    # Manual + good rating + many reviews + phone
assert pri(auto="Semi Automated") == "High"
assert pri(rating=PRIORITY_HIGH_MIN_RATING, reviews=PRIORITY_HIGH_MIN_REVIEWS) == "High"   # boundaries count

print("Low")
assert pri(auto="Automated") == "Low"                     # already automated, even with great reviews
assert pri(auto="Automated", rating=None, reviews=None) == "Low"
assert pri(phone="") == "Low"                             # nobody to call

print("Medium")
assert pri(auto="Unknown") == "Medium"                    # cannot tell
assert pri(auto="") == "Medium"
assert pri(rating=3.9) == "Medium"
assert pri(reviews=PRIORITY_HIGH_MIN_REVIEWS - 1) == "Medium"
assert pri(rating=None) == "Medium"
assert pri(reviews=None) == "Medium"
assert pri(rating="4.6", reviews="120") == "High"         # numbers saved as text still work
assert pri(rating="n/a", reviews="many") == "Medium"      # junk values do not crash

print("Reasons")
p, why = score_lead(mk())
assert p == "High" and "Manual" in why and "4.5 stars" in why and "200 reviews" in why and "phone ok" in why
p, why = score_lead(mk(auto="Unknown", rating=3.8))
assert p == "Medium" and "automation unclear" in why and "rating under 4" in why
p, why = score_lead(mk(auto="Automated"))
assert p == "Low" and "already Automated" in why

print("Batch: every lead gets a priority, Call Status 'Not Called', Follow-up Date blank")
leads = [
    mk(lead_id="LD-0001"),
    mk(lead_id="LD-0002", auto="Automated"),
    mk(lead_id="LD-0003", auto="Unknown", rating=None, reviews=None),
    mk(lead_id="LD-0004", auto="Semi Automated", rating=4.1, reviews=10),
    mk(lead_id="LD-0005", phone=""),
]
leads[0].call_status = ""            # blank -> filled in
leads[3].call_status = "Not Called"
score_leads(leads)
assert [l.lead_priority for l in leads] == ["High", "Low", "Medium", "Medium", "Low"]
assert all(l.call_status == "Not Called" for l in leads)
assert all(l.follow_up_date == "" for l in leads)
assert validate_priority(leads) == []

print("Sheet row")
row = leads[0].to_sheet_dict()
assert row["Lead Priority"] == "High" and row["Call Status"] == "Not Called" and row["Follow-up Date"] == ""
assert "Priority High" in row["Notes"]
leads[0].detection_note = "Has WhatsApp button, no cart"
assert leads[0].to_sheet_dict()["Notes"].startswith("Has WhatsApp button, no cart | Priority High")

print("Done check catches problems")
bad = mk(lead_id="LD-0009")
assert validate_priority([bad]) and not priority_report([bad])["complete"]
assert not priority_report([])["complete"]

report = priority_report(leads)
assert report["complete"] and report["counts"] == {"High": 1, "Medium": 2, "Low": 2}
assert report["not_called"] == 5 and report["follow_up_filled"] == 0
print(f"\nChunk 8 complete: {report['total_leads']} test leads scored -> {report['counts']}")
