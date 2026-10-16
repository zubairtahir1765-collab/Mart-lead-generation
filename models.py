"""Lead data structure shared by all chunks."""
from dataclasses import dataclass, field, asdict


@dataclass
class Lead:
    lead_id: str = ""          # assigned in Chunk 5

    # --- from search (Chunk 3) ---
    place_id: str = ""
    business_type: str = ""
    country: str = ""
    city: str = ""

    # --- from place details (Chunk 4) ---
    business_name: str = ""
    address: str = ""
    phone_number: str = ""
    phone_national: str = ""
    phone_e164: str = ""       # validated, normalised in Chunk 5 (+441131234567)
    website_url: str = ""
    google_rating: float | None = None
    reviews_count: int | None = None
    google_maps_url: str = ""
    business_status: str = ""

    # --- Chunk 6: website and social enrichment ---
    email: str = ""
    owner_name: str = ""
    social_links: str = ""          # "Instagram: <url> | Facebook: <url>"
    website_status: str = ""        # ok / unreachable / blocked / social_only / none (not a sheet column)
    extra_emails: str = ""          # other emails found, goes into Notes

    # --- Chunk 7: detection inputs/outputs (not sheet columns) ---
    site_evidence: dict = field(default_factory=dict)   # keywords/links found on the site (see detection.py)
    platform_hint: str = ""         # Google's "website" was really a delivery platform page, e.g. "Deliveroo"
    detection_note: str = ""        # short reason for order method / delivery, goes into Notes

    # --- filled by later chunks ---
    order_method: str = ""
    delivery_system: str = ""
    automation_status: str = ""
    lead_priority: str = ""
    priority_note: str = ""         # Chunk 8: short reason for High/Medium/Low, goes into Notes
    notes: str = ""
    call_status: str = "Not Called"
    follow_up_date: str = ""

    @property
    def website_available(self) -> str:
        return "Yes" if self.website_url else "No"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["website_available"] = self.website_available
        return data

    def to_sheet_dict(self) -> dict:
        """Keys match SHEET_COLUMNS exactly, ready for sheets.append_lead()."""
        notes = self.notes
        if self.detection_note:
            notes = f"{notes} | {self.detection_note}" if notes else self.detection_note
        if self.priority_note:
            notes = f"{notes} | {self.priority_note}" if notes else self.priority_note
        if self.extra_emails:
            extra = f"Other emails: {self.extra_emails}"
            notes = f"{notes} | {extra}" if notes else extra
        if self.website_status in ("unreachable", "blocked"):
            flag = f"Website {self.website_status}"
            notes = f"{notes} | {flag}" if notes else flag
        if self.google_maps_url:
            maps = f"Maps: {self.google_maps_url}"
            notes = f"{notes} | {maps}" if notes else maps
        return {
            "Lead ID": self.lead_id,
            "Business Name": self.business_name,
            "Business Type": self.business_type,
            "Country": self.country,
            "City": self.city,
            "Address": self.address,
            "Phone Number": self.phone_number,
            "Email": self.email,
            "Website Available": self.website_available,
            "Website URL": self.website_url,
            "Owner/Manager Name": self.owner_name,
            "Social Media Links": self.social_links,
            "Order Method": self.order_method,
            "Delivery System": self.delivery_system,
            "Automation Status": self.automation_status,
            "Google Rating": self.google_rating if self.google_rating is not None else "",
            "Reviews Count": self.reviews_count if self.reviews_count is not None else "",
            "Lead Priority": self.lead_priority,
            "Notes": notes,
            "Call Status": self.call_status,
            "Follow-up Date": self.follow_up_date,
        }
