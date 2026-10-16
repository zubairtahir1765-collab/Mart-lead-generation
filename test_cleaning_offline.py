"""Chunk 5 offline check: no API key needed. Uses made-up leads with problems on purpose."""
from cleaning import clean_leads, normalize_phone, validate_output
from models import Lead


def L(name, addr, phone, country="United Kingdom", pid="", reviews=0, site=""):
    return Lead(place_id=pid, business_name=name, address=addr, phone_number=phone,
                country=country, city="Leeds", business_type="Grocery Store",
                reviews_count=reviews, website_url=site)


samples = [
    L("Asian  Mart Ltd", "1 High St, Leeds LS1 1AA", "0113 496 0001", pid="a"),            # good
    L("Asian Mart",      "1 High Street, Leeds LS1 1AA", "+44 113 496 0001", pid="b",
      reviews=50, site="https://asianmart.co.uk"),                                          # duplicate phone (better record)
    L("Fresh Foods",     "5 Park Rd, Leeds", "", pid="c"),                                   # missing phone
    L("Bad Number Shop", "9 Mill Ln, Leeds", "12345", pid="d"),                              # invalid
    L("Dubai Number",    "2 King St, Leeds", "+971 4 123 4567", pid="e"),                    # wrong country (UAE number for UK lead)
    L("City Grocers",    "7 Bridge St, Leeds", "0113 496 0002", pid="f"),                    # good
    L("City Grocers",    "7 Bridge St, Leeds", "0113 496 0003", pid="g"),                    # duplicate name+address
    L("Karachi Mart",    "Block 5, Clifton, Karachi", "021 35871234", country="Pakistan", pid="h"),  # good (PK)
]

print("Phone checks:")
for raw, ctry in [("0113 496 0001", "United Kingdom"), ("+971 4 123 4567", "United Kingdom"),
                  ("021 35871234", "Pakistan"), ("12345", "United Kingdom"), ("", "UAE")]:
    print(f"  {raw!r:20} {ctry:15} -> {normalize_phone(raw, ctry)}")

clean, rejected, stats = clean_leads(samples, start_id=0)
print("\nStats:", stats)
print("\nKept:")
for l in clean:
    print(f"  {l.lead_id} | {l.business_name} | {l.phone_number} | reviews={l.reviews_count}")
print("\nRejected:")
for r in rejected:
    print(f"  {r['reason']:<20} {r['name']}")

problems = validate_output(clean)
assert not problems, problems
assert stats["output"] == 3, stats
print("\nChunk 5 offline check passed: no duplicates, no invalid phones.")
