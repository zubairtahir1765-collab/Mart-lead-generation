"""Google Sheets helper: connect, set up headers/formatting, append leads."""
import sys

import gspread
from google.oauth2.service_account import Credentials

from config import (
    DROPDOWNS,
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SHEET_ID,
    SHEET_COLUMNS,
    WORKSHEET_NAME,
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_spreadsheet():
    if not GOOGLE_SHEET_ID or GOOGLE_SHEET_ID == "your_sheet_id_here":
        sys.exit("ERROR: GOOGLE_SHEET_ID is missing in .env")
    try:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
    except FileNotFoundError:
        sys.exit(f"ERROR: '{GOOGLE_CREDENTIALS_FILE}' not found. Download your service account JSON key.")
    client = gspread.authorize(creds)
    try:
        return client.open_by_key(GOOGLE_SHEET_ID)
    except gspread.exceptions.APIError as e:
        sys.exit(
            "ERROR: could not open the sheet. Did you share it with the service account "
            f"email (Editor access)?\n{e}"
        )


def get_worksheet():
    """Return the Leads worksheet, creating it if it does not exist."""
    spreadsheet = get_spreadsheet()
    try:
        return spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(
            title=WORKSHEET_NAME, rows=1000, cols=len(SHEET_COLUMNS)
        )


def ensure_headers(ws):
    """Write the 21 header columns in row 1 if they are missing or different."""
    current = ws.row_values(1)
    if current != SHEET_COLUMNS:
        end_col = gspread.utils.rowcol_to_a1(1, len(SHEET_COLUMNS))
        ws.update(range_name=f"A1:{end_col}", values=[SHEET_COLUMNS])
        print("Headers written.")
    else:
        print("Headers already correct.")


def format_sheet(ws):
    """Bold + frozen header, column widths, and dropdowns for status columns."""
    sheet_id = ws.id
    requests = [
        # freeze header row
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # bold, coloured header
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # auto-resize columns
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(SHEET_COLUMNS),
                }
            }
        },
        # basic filter on the whole table
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(SHEET_COLUMNS),
                    }
                }
            }
        },
    ]

    # dropdown validation (rows 2 to 1000)
    for column_name, options in DROPDOWNS.items():
        col_index = SHEET_COLUMNS.index(column_name)
        requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 1000,
                        "startColumnIndex": col_index,
                        "endColumnIndex": col_index + 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": o} for o in options],
                        },
                        "showCustomUi": True,
                        "strict": False,  # not strict, so free-text like "Walk-in + Phone" still works
                    },
                }
            }
        )

    ws.spreadsheet.batch_update({"requests": requests})
    print("Formatting and dropdowns applied.")


def next_lead_id(ws):
    """Generate the next Lead ID like LD-0001 based on existing rows."""
    existing = ws.col_values(1)[1:]  # skip header
    numbers = []
    for value in existing:
        if value.startswith("LD-") and value[3:].isdigit():
            numbers.append(int(value[3:]))
    return f"LD-{(max(numbers) + 1 if numbers else 1):04d}"


def text_safe(value):
    """Google Sheets treats text starting with = + - @ as a formula (a phone like '+44 20 7946 0958' became #ERROR!).
    A leading apostrophe makes it plain text; the apostrophe itself is not stored in the cell."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def append_lead(ws, lead: dict):
    """Append one lead (dict keyed by column name). Missing fields stay blank."""
    if not lead.get("Lead ID"):
        lead["Lead ID"] = next_lead_id(ws)
    row = [text_safe(lead.get(col, "")) for col in SHEET_COLUMNS]
    ws.append_row(row, value_input_option="USER_ENTERED")
    return lead["Lead ID"]


def append_leads(ws, leads: list[dict]) -> int:
    """Append many leads (dicts keyed by column name) in ONE API call. Lead IDs must already be set."""
    if not leads:
        return 0
    rows = [[text_safe(lead.get(col, "")) for col in SHEET_COLUMNS] for lead in leads]
    ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
    return len(rows)


def max_lead_number(ws) -> int:
    """Highest existing Lead ID number in the sheet (0 if empty). Use as start_id for Chunk 5."""
    numbers = []
    for value in ws.col_values(1)[1:]:
        if value.startswith("LD-") and value[3:].isdigit():
            numbers.append(int(value[3:]))
    return max(numbers) if numbers else 0
