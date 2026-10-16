"""Repair phone numbers that show #ERROR! in the sheet (they were written as "+44 ..." and read as formulas).

    python fix_phone_errors.py --dry-run     # show what would change, write nothing
    python fix_phone_errors.py               # fix them

How each broken cell is recovered:
  1. Google still keeps what was typed ("=+44 20 7946 0958"): read it back with the FORMULA view, or
  2. if that is not available, take the phone saved for the same Lead ID in data/enriched/*.json.
Every recovered number is re-checked (valid and from the lead's country) and rewritten as plain text.
Rows whose phone is fine are not touched.
"""
import argparse
import glob
import json
import os

from cleaning import normalize_phone
from config import ENRICHED_DATA_DIR, SHEET_COLUMNS

PHONE_COL = SHEET_COLUMNS.index("Phone Number") + 1
COUNTRY_COL = SHEET_COLUMNS.index("Country") + 1
ID_COL = SHEET_COLUMNS.index("Lead ID") + 1


def _a1(col: int, row: int) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def saved_phones(folder: str = ENRICHED_DATA_DIR) -> dict[str, str]:
    """Lead ID -> phone, from the batches the pipeline saved."""
    out = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                for lead in json.load(f):
                    if lead.get("lead_id") and lead.get("phone_number"):
                        out[lead["lead_id"]] = lead["phone_number"]
        except (OSError, ValueError):
            continue
    return out


def repair(ws, dry_run: bool = False, folder: str = ENRICHED_DATA_DIR) -> dict:
    shown = ws.col_values(PHONE_COL)                                   # what you see in the sheet
    typed = ws.col_values(PHONE_COL, value_render_option="FORMULA")    # what was typed (formula text)
    countries = ws.col_values(COUNTRY_COL)
    ids = ws.col_values(ID_COL)
    backup = saved_phones(folder)

    updates, stats = [], {"checked": 0, "broken": 0, "fixed": 0, "unrecoverable": []}
    for i in range(1, len(shown)):                                      # skip header
        stats["checked"] += 1
        view = shown[i].strip()
        raw_typed = typed[i].strip() if i < len(typed) else ""
        if not (view.startswith("#") or raw_typed.startswith("=")):
            continue                                                    # phone is fine
        stats["broken"] += 1
        country = countries[i] if i < len(countries) else ""
        lead_id = ids[i] if i < len(ids) else ""
        candidates = [raw_typed.lstrip("="), backup.get(lead_id, "")]
        fixed = None
        for cand in candidates:
            if cand:
                e164, intl, reason = normalize_phone(cand, country)
                if reason == "ok":
                    fixed = intl
                    break
        if fixed:
            updates.append({"range": _a1(PHONE_COL, i + 1), "values": [["'" + fixed]]})
            stats["fixed"] += 1
        else:
            stats["unrecoverable"].append(lead_id or f"row {i + 1}")

    if updates and not dry_run:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = parser.parse_args()

    from sheets import get_worksheet
    result = repair(get_worksheet(), dry_run=args.dry_run)
    verb = "would fix" if args.dry_run else "fixed"
    print(f"Rows checked: {result['checked']} | broken phones: {result['broken']} | {verb}: {result['fixed']}")
    if result["unrecoverable"]:
        print("Could not recover (check these by hand):", ", ".join(result["unrecoverable"]))
