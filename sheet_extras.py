"""Chunk 10: sheet polish. Dropdowns, Lead Priority colors and ready-made filter views.

    refresh_sheet_extras(ws)     # safe to run any number of times; run_weekly.py calls it after adding rows

What it does, and how it behaves when repeated:
  * Dropdowns (Call Status, Lead Priority, ...): re-applied to the whole current sheet. Chunk 2 stopped at row 1000;
    this keeps them going as the sheet grows. Values come from DROPDOWNS in config.py.
  * Lead Priority colors: High green, Medium amber, Low grey. A color rule is only added if that rule is missing, so
    nothing is duplicated and a color you changed by hand is left alone.
  * Filter views (Data > Filter views): "To call: High priority" and "Follow-ups due". Created only if missing, so
    edits you make to them stay.
  * The basic filter on the header row is NOT touched here (setup_sheet.py creates it once). Re-setting it every week
    would clear whatever filter you have applied while working.

This module only calls methods of the worksheet object (id, spreadsheet.batch_update, spreadsheet.fetch_sheet_metadata),
so it has no gspread import and can be tested with a fake worksheet.
"""
from config import DROPDOWNS, SHEET_COLUMNS

PRIORITY_COLUMN = "Lead Priority"
CALL_STATUS_COLUMN = "Call Status"

# value -> (background, text) as 0-1 RGB
PRIORITY_COLORS = {
    "High": ((0.72, 0.88, 0.80), (0.05, 0.40, 0.20)),      # green: best fit, call first
    "Medium": ((1.00, 0.95, 0.80), (0.50, 0.38, 0.00)),    # amber
    "Low": ((0.90, 0.90, 0.90), (0.40, 0.40, 0.40)),       # grey: already automated or no phone
}

# (view title, {column: value that must match}, column to sort by, sort order)
FILTER_VIEWS = [
    ("To call: High priority", {PRIORITY_COLUMN: "High", CALL_STATUS_COLUMN: "Not Called"},
     "Reviews Count", "DESCENDING"),
    ("Follow-ups due", {CALL_STATUS_COLUMN: "Follow-up"}, "Follow-up Date", "ASCENDING"),
]


def _col(name: str) -> int:
    return SHEET_COLUMNS.index(name)


def _rgb(t):
    return {"red": t[0], "green": t[1], "blue": t[2]}


# ---------------------------------------------------------------- request builders (pure, easy to test)
def dropdown_requests(sheet_id: int) -> list[dict]:
    """Dropdown on every row below the header, to the end of the sheet. Not strict, so free text still works."""
    requests = []
    for column_name, options in DROPDOWNS.items():
        i = _col(column_name)
        requests.append({"setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": i, "endColumnIndex": i + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": o} for o in options]},
                     "showCustomUi": True, "strict": False},
        }})
    return requests


def _priority_values_present(conditional_formats: list[dict]) -> set[str]:
    """Which of High/Medium/Low already have a color rule on the Lead Priority column."""
    col = _col(PRIORITY_COLUMN)
    found = set()
    for rule in conditional_formats or []:
        cond = (rule.get("booleanRule") or {}).get("condition", {})
        values = cond.get("values") or [{}]
        on_column = any(r.get("startColumnIndex") == col and r.get("endColumnIndex") == col + 1
                        for r in rule.get("ranges", []))
        if on_column and cond.get("type") == "TEXT_EQ":
            found.add(values[0].get("userEnteredValue"))
    return found


def priority_color_requests(sheet_id: int, conditional_formats: list[dict]) -> list[dict]:
    """addConditionalFormatRule for each priority that has no rule yet."""
    col = _col(PRIORITY_COLUMN)
    present = _priority_values_present(conditional_formats)
    requests = []
    for value, (background, text) in PRIORITY_COLORS.items():
        if value in present:
            continue
        requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1}],
            "booleanRule": {
                "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": value}]},
                "format": {"backgroundColor": _rgb(background),
                           "textFormat": {"foregroundColor": _rgb(text), "bold": True}},
            },
        }}})
    return requests


def filter_view_requests(sheet_id: int, existing_views: list[dict]) -> list[dict]:
    """addFilterView for each ready-made view that does not exist yet (matched by title)."""
    have = {v.get("title") for v in existing_views or []}
    requests = []
    for title, conditions, sort_column, order in FILTER_VIEWS:
        if title in have:
            continue
        criteria = {str(_col(name)): {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": value}]}}
                    for name, value in conditions.items()}
        requests.append({"addFilterView": {"filter": {
            "title": title,
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0,
                      "endColumnIndex": len(SHEET_COLUMNS)},
            "criteria": criteria,
            "sortSpecs": [{"dimensionIndex": _col(sort_column), "sortOrder": order}],
        }}})
    return requests


# ---------------------------------------------------------------- the one call
def _sheet_metadata(ws) -> dict:
    """The conditional formats and filter views that already exist on this worksheet."""
    meta = ws.spreadsheet.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId),conditionalFormats,filterViews(filterViewId,title))"})
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == ws.id:
            return sheet
    return {}


def refresh_sheet_extras(ws) -> dict:
    """Apply dropdowns, priority colors and filter views in ONE batch_update. Returns what was added."""
    existing = _sheet_metadata(ws)
    color_reqs = priority_color_requests(ws.id, existing.get("conditionalFormats", []))
    view_reqs = filter_view_requests(ws.id, existing.get("filterViews", []))
    requests = dropdown_requests(ws.id) + color_reqs + view_reqs
    ws.spreadsheet.batch_update({"requests": requests})
    return {"dropdowns": len(DROPDOWNS), "color_rules_added": len(color_reqs), "filter_views_added": len(view_reqs)}
