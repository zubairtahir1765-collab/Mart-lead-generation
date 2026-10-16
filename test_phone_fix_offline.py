"""Phone #ERROR! fix, offline check: text_safe, the writers, and the repair tool (fake sheet, no Google needed)."""
import json
import os
import tempfile

from config import SHEET_COLUMNS
from fix_phone_errors import repair
from sheets import append_leads, text_safe

P = SHEET_COLUMNS.index("Phone Number")

print("1. text_safe")
assert text_safe("+44 20 7946 0958") == "'+44 20 7946 0958"
assert text_safe("=SUM(A1)") == "'=SUM(A1)" and text_safe("-5 off") == "'-5 off" and text_safe("@x") == "'@x"
assert text_safe("Fresh Foods") == "Fresh Foods" and text_safe("") == "" and text_safe(4.5) == 4.5 and text_safe(0) == 0

print("2. append_leads protects the phone column")
class WS:
    def __init__(self): self.rows = []
    def append_rows(self, rows, **kw): assert kw["value_input_option"] == "USER_ENTERED"; self.rows += rows
ws = WS()
append_leads(ws, [{"Lead ID": "LD-0001", "Business Name": "X", "Phone Number": "+44 20 7946 0958", "Google Rating": 4.5}])
assert ws.rows[0][P] == "'+44 20 7946 0958" and ws.rows[0][SHEET_COLUMNS.index("Google Rating")] == 4.5

print("3. repair tool")
class Sheet:
    """Column data as Google gives it: shown values and typed (formula) values."""
    def __init__(self, shown, typed, countries, ids):
        self.cols = {"shown": shown, "typed": typed}
        self.countries, self.ids, self.updates = countries, ids, []
    def col_values(self, col, value_render_option=None):
        if col == SHEET_COLUMNS.index("Country") + 1: return self.countries
        if col == SHEET_COLUMNS.index("Lead ID") + 1: return self.ids
        return self.cols["typed" if value_render_option == "FORMULA" else "shown"]
    def batch_update(self, updates, value_input_option=None):
        assert value_input_option == "USER_ENTERED"; self.updates = updates

hdr = "Phone Number"
shown = [hdr, "#ERROR!", "#ERROR!", "+44 113 496 0001", "#ERROR!", "#ERROR!"]
typed = [hdr, "=+44 113 496 0002", "=+44 113 496 0003", "+44 113 496 0001", "", "=+44 12345"]
countries = ["Country"] + ["United Kingdom"] * 5
ids = ["Lead ID", "LD-0001", "LD-0002", "LD-0003", "LD-0004", "LD-0005"]
tmp = tempfile.mkdtemp()
json.dump([{"lead_id": "LD-0004", "phone_number": "+44 113 496 0004"}], open(os.path.join(tmp, "b.json"), "w"))

dry = Sheet(shown, typed, countries, ids)
r = repair(dry, dry_run=True, folder=tmp)
assert r["broken"] == 4 and r["fixed"] == 3 and r["unrecoverable"] == ["LD-0005"] and dry.updates == [], r   # dry run writes nothing
live = Sheet(shown, typed, countries, ids)
r = repair(live, folder=tmp)
assert [u["range"] for u in live.updates] == ["G2", "G3", "G5"], live.updates
assert live.updates[0]["values"] == [["'+44 113 496 0002"]]
assert live.updates[2]["values"] == [["'+44 113 496 0004"]]                 # recovered from the saved batch
print("\nPhone fix offline check passed.")
