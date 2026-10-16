"""Chunk 10 offline check: the sheet extras (dropdowns, Lead Priority colors, filter views).

Uses a fake worksheet that records the batch_update request and reports back what "exists" like the Sheets API
would, so no Google account is needed. It checks the requests we build; the first real run
(`python setup_sheet.py`) is what proves Google accepts them. See the README.

Usage:
    python test_sheet_extras_offline.py
"""
import json

from config import DROPDOWNS, SHEET_COLUMNS
from sheet_extras import refresh_sheet_extras

SHEET_ID = 7
PRIORITY_COL = SHEET_COLUMNS.index("Lead Priority")
STATUS_COL = SHEET_COLUMNS.index("Call Status")


class FakeSpreadsheet:
    def __init__(self, sheets_meta=None):
        self.sheets_meta = sheets_meta or []
        self.batches = []
        self.metadata_params = None

    def fetch_sheet_metadata(self, params=None):
        self.metadata_params = params
        return {"sheets": self.sheets_meta}

    def batch_update(self, body):
        self.batches.append(body["requests"])
        return {}

    def apply_like_google(self, requests):
        """What the API would then report for rules and views we asked it to add."""
        rules, views = [], []
        for r in requests:
            if "addConditionalFormatRule" in r:
                rules.append(r["addConditionalFormatRule"]["rule"])
            if "addFilterView" in r:
                views.append({"filterViewId": 100 + len(views), "title": r["addFilterView"]["filter"]["title"]})
        return rules, views


class FakeWs:
    id = SHEET_ID

    def __init__(self, sheets_meta=None):
        self.spreadsheet = FakeSpreadsheet(sheets_meta)


def kinds(requests):
    out = {}
    for r in requests:
        k = next(iter(r))
        out[k] = out.get(k, 0) + 1
    return out


# ---------------------------------------------------------------- 1. first run on a plain sheet
print("1. First run adds dropdowns, 3 color rules and 2 filter views in ONE batch")
assert PRIORITY_COL == 17 and STATUS_COL == 19, "the column positions this module relies on"
ws = FakeWs([{"properties": {"sheetId": SHEET_ID}}])
result = refresh_sheet_extras(ws)
assert len(ws.spreadsheet.batches) == 1, "one API call"
reqs = ws.spreadsheet.batches[0]
assert kinds(reqs) == {"setDataValidation": len(DROPDOWNS), "addConditionalFormatRule": 3, "addFilterView": 2}, kinds(reqs)
assert result == {"dropdowns": len(DROPDOWNS), "color_rules_added": 3, "filter_views_added": 2}
json.dumps(reqs)                                   # the request must be plain JSON
assert "conditionalFormats" in ws.spreadsheet.metadata_params["fields"]

print("2. Dropdowns: Call Status has its options, every row below the header, no end row (grows with the sheet)")
dd = {r["setDataValidation"]["range"]["startColumnIndex"]: r["setDataValidation"] for r in reqs if "setDataValidation" in r}
call = dd[STATUS_COL]
assert [v["userEnteredValue"] for v in call["rule"]["condition"]["values"]] == DROPDOWNS["Call Status"]
assert call["rule"]["condition"]["type"] == "ONE_OF_LIST" and call["rule"]["showCustomUi"] is True
assert call["rule"]["strict"] is False, "free text must still be allowed"
assert call["range"]["sheetId"] == SHEET_ID and call["range"]["startRowIndex"] == 1
assert "endRowIndex" not in call["range"], "an end row would stop the dropdown at that row"
assert set(dd) == {SHEET_COLUMNS.index(c) for c in DROPDOWNS}

print("3. Priority colors: right column, right values, header row excluded")
rules = [r["addConditionalFormatRule"]["rule"] for r in reqs if "addConditionalFormatRule" in r]
by_value = {r["booleanRule"]["condition"]["values"][0]["userEnteredValue"]: r for r in rules}
assert set(by_value) == {"High", "Medium", "Low"}
for r in rules:
    rng = r["ranges"][0]
    assert (rng["sheetId"], rng["startColumnIndex"], rng["endColumnIndex"], rng["startRowIndex"]) == \
        (SHEET_ID, PRIORITY_COL, PRIORITY_COL + 1, 1) and "endRowIndex" not in rng
    assert r["booleanRule"]["condition"]["type"] == "TEXT_EQ"
high = by_value["High"]["booleanRule"]["format"]["backgroundColor"]
low = by_value["Low"]["booleanRule"]["format"]["backgroundColor"]
assert high["green"] > high["red"] and high["green"] > high["blue"], "High is green"
assert abs(low["red"] - low["green"]) < 0.01 and abs(low["green"] - low["blue"]) < 0.01, "Low is grey"
assert by_value["High"]["booleanRule"]["format"]["textFormat"]["bold"] is True
assert len({json.dumps(r["booleanRule"]["format"]) for r in rules}) == 3, "three distinct looks"

print("4. Filter views: criteria on the right columns, sorted")
views = {r["addFilterView"]["filter"]["title"]: r["addFilterView"]["filter"] for r in reqs if "addFilterView" in r}
call_list = views["To call: High priority"]
assert call_list["criteria"] == {
    str(PRIORITY_COL): {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "High"}]}},
    str(STATUS_COL): {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "Not Called"}]}}}
assert call_list["sortSpecs"] == [{"dimensionIndex": SHEET_COLUMNS.index("Reviews Count"), "sortOrder": "DESCENDING"}]
follow = views["Follow-ups due"]
assert follow["criteria"] == {str(STATUS_COL): {"condition": {"type": "TEXT_EQ",
                                                              "values": [{"userEnteredValue": "Follow-up"}]}}}
assert follow["sortSpecs"] == [{"dimensionIndex": SHEET_COLUMNS.index("Follow-up Date"), "sortOrder": "ASCENDING"}]
for v in views.values():
    assert v["range"] == {"sheetId": SHEET_ID, "startRowIndex": 0, "startColumnIndex": 0,
                          "endColumnIndex": len(SHEET_COLUMNS)}
assert "Not Called" in DROPDOWNS["Call Status"] and "Follow-up" in DROPDOWNS["Call Status"], \
    "the filter views must match values the dropdown actually offers"
assert {"High", "Medium", "Low"} == set(DROPDOWNS["Lead Priority"])

# ---------------------------------------------------------------- 5. weekly re-runs are idempotent
print("5. Running again adds no duplicates (only the dropdowns are re-applied)")
rules_now, views_now = ws.spreadsheet.apply_like_google(reqs)
ws2 = FakeWs([{"properties": {"sheetId": SHEET_ID}, "conditionalFormats": rules_now, "filterViews": views_now}])
result2 = refresh_sheet_extras(ws2)
assert result2["color_rules_added"] == 0 and result2["filter_views_added"] == 0, result2
assert kinds(ws2.spreadsheet.batches[0]) == {"setDataValidation": len(DROPDOWNS)}

print("6. A color you changed by hand is not overwritten; a missing rule is restored")
tweaked = json.loads(json.dumps(rules_now))
for r in tweaked:
    if r["booleanRule"]["condition"]["values"][0]["userEnteredValue"] == "High":
        r["booleanRule"]["format"]["backgroundColor"] = {"red": 0, "green": 0, "blue": 1}      # user made it blue
tweaked = [r for r in tweaked if r["booleanRule"]["condition"]["values"][0]["userEnteredValue"] != "Low"]
ws3 = FakeWs([{"properties": {"sheetId": SHEET_ID}, "conditionalFormats": tweaked, "filterViews": views_now}])
result3 = refresh_sheet_extras(ws3)
added = [r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
         for r in ws3.spreadsheet.batches[0] if "addConditionalFormatRule" in r]
assert added == ["Low"] and result3["color_rules_added"] == 1, added

print("7. Other columns' rules and other sheets' rules do not count; unrelated rules are never deleted")
other_col = {"ranges": [{"sheetId": SHEET_ID, "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 4}],
             "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "High"}]},
                             "format": {}}}
ws4 = FakeWs([{"properties": {"sheetId": 999}, "conditionalFormats": rules_now, "filterViews": views_now},
              {"properties": {"sheetId": SHEET_ID}, "conditionalFormats": [other_col], "filterViews": []}])
result4 = refresh_sheet_extras(ws4)
assert result4["color_rules_added"] == 3 and result4["filter_views_added"] == 2, \
    "only this worksheet's rules are looked at, and a 'High' rule on another column is not a priority rule"
assert not any("delete" in next(iter(r)).lower() for r in ws4.spreadsheet.batches[0]), "nothing is ever deleted"

print("8. The basic filter is left alone (re-setting it weekly would clear the filter someone is using)")
assert not any("BasicFilter" in next(iter(r)) for r in reqs)

print("9. A sheet the API does not list yet is handled")
ws5 = FakeWs([])
assert refresh_sheet_extras(ws5)["color_rules_added"] == 3

print("\nChunk 10 sheet extras offline check passed.")
