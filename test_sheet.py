"""Appends one dummy row to confirm Chunk 2 works."""
from sheets import append_lead, get_worksheet

DUMMY_LEAD = {
    "Business Name": "TEST - Dummy Mart",
    "Business Type": "Asian Grocery Store",
    "Country": "United Kingdom",
    "City": "Leeds",
    "Address": "1 Test Street, Leeds, UK",
    "Phone Number": "+44 113 000 0000",
    "Website Available": "No",
    "Order Method": "Walk-in + Phone",
    "Delivery System": "Unknown",
    "Automation Status": "Manual",
    "Google Rating": 4.5,
    "Reviews Count": 120,
    "Lead Priority": "High",
    "Notes": "Dummy row from test_sheet.py - safe to delete",
    "Call Status": "Not Called",
}

if __name__ == "__main__":
    ws = get_worksheet()
    lead_id = append_lead(ws, dict(DUMMY_LEAD))
    print(f"Appended {lead_id} to '{ws.title}'.")
    print(f"Open your sheet: {ws.spreadsheet.url}")
    print("Chunk 2 complete. You can delete the TEST row manually.")
