"""Run once: writes the 21 headers, applies formatting/dropdowns, then the Chunk 10 extras
(Lead Priority colors, extended dropdowns, ready-made filter views). Safe to run again at any time."""
from sheet_extras import refresh_sheet_extras
from sheets import ensure_headers, format_sheet, get_worksheet

if __name__ == "__main__":
    ws = get_worksheet()
    ensure_headers(ws)
    format_sheet(ws)
    added = refresh_sheet_extras(ws)
    print(f"Extras: dropdowns refreshed, {added['color_rules_added']} priority color rule(s) added, "
          f"{added['filter_views_added']} filter view(s) added.")
    print(f"Sheet ready: {ws.spreadsheet.url}")
