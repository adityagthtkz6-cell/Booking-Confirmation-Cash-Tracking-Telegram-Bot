"""
setup_dashboard.py
Run once to create/refresh the Dashboard tab in Google Sheets.
The dashboard auto-updates via formulas — no need to run again unless you
want to reset the layout.

Usage:  python setup_dashboard.py
"""
import sys
import os

sys.path.insert(0, ".")

import config
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DASHBOARD = "Dashboard"
BOOKINGS  = "Bookings"

# ── Colour helpers ────────────────────────────────────────────────────────────
def _rgb(r, g, b):
    return {"red": round(r / 255, 4), "green": round(g / 255, 4), "blue": round(b / 255, 4)}

NAVY        = _rgb(26,  43,  76)
DARK_BLUE   = _rgb(31,  73, 125)
LIGHT_BLUE  = _rgb(189, 215, 238)
GREEN       = _rgb(56,  168, 105)
RED         = _rgb(207,  74,  68)
AMBER       = _rgb(255, 179,  28)
GREY        = _rgb(242, 242, 242)
WHITE       = {"red": 1.0, "green": 1.0, "blue": 1.0}
BLACK       = {"red": 0.0, "green": 0.0, "blue": 0.0}

# ── Month formula fragments ───────────────────────────────────────────────────
_MOM_S = "DATE(YEAR(TODAY()),MONTH(TODAY()),1)"
_MOM_E = "EOMONTH(TODAY(),0)"

# ── Cell value grid (row, col → value/formula) ───────────────────────────────
# Rows are 0-indexed for the API; displayed here as human-readable (row+1)
CELL_VALUES = {
    # ── Title ────────────────────────────────────────────────────────────────
    (0, 0): "📊  Booking Dashboard — Sahm Plus Transportation",
    (0, 4): "=TODAY()",

    # ── TODAY snapshot ───────────────────────────────────────────────────────
    (2, 0): "📅  TODAY'S SNAPSHOT",

    (3, 0): "CONFIRMED",
    (3, 1): "NO-SHOWS",
    (3, 2): "PENDING",
    (3, 3): "REVENUE COLLECTED (QAR)",

    (4, 0): f'=COUNTIFS({BOOKINGS}!$B:$B,TODAY(),{BOOKINGS}!$H:$H,"Yes")',
    (4, 1): f'=COUNTIFS({BOOKINGS}!$B:$B,TODAY(),{BOOKINGS}!$H:$H,"No-show")',
    (4, 2): (
        f'=COUNTIF({BOOKINGS}!$B:$B,TODAY())'
        f'-COUNTIFS({BOOKINGS}!$B:$B,TODAY(),{BOOKINGS}!$H:$H,"Yes")'
        f'-COUNTIFS({BOOKINGS}!$B:$B,TODAY(),{BOOKINGS}!$H:$H,"No-show")'
    ),
    (4, 3): f'=SUMIFS({BOOKINGS}!$K:$K,{BOOKINGS}!$B:$B,TODAY())',

    # ── THIS MONTH ───────────────────────────────────────────────────────────
    (6, 0): "📆  THIS MONTH",

    (7, 0): "CONFIRMED",
    (7, 1): "NO-SHOWS",
    (7, 2): "TOTAL REVENUE (QAR)",
    (7, 3): "CASH COLLECTED",
    (7, 4): "BANK TRANSFERS",
    (7, 5): "TOTAL TOURS",

    (8, 0): (
        f'=COUNTIFS({BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E},'
        f'{BOOKINGS}!$H:$H,"Yes")'
    ),
    (8, 1): (
        f'=COUNTIFS({BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E},'
        f'{BOOKINGS}!$H:$H,"No-show")'
    ),
    (8, 2): (
        f'=SUMIFS({BOOKINGS}!$K:$K,'
        f'{BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E})'
    ),
    (8, 3): (
        f'=SUMIFS({BOOKINGS}!$K:$K,'
        f'{BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E},'
        f'{BOOKINGS}!$L:$L,"Cash")'
    ),
    (8, 4): (
        f'=SUMIFS({BOOKINGS}!$K:$K,'
        f'{BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E},'
        f'{BOOKINGS}!$L:$L,"Bank Transfer")'
    ),
    (8, 5): (
        f'=COUNTIFS({BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E},'
        f'{BOOKINGS}!$H:$H,"Yes")'
        f'+COUNTIFS({BOOKINGS}!$B:$B,">="&{_MOM_S},'
        f'{BOOKINGS}!$B:$B,"<="&{_MOM_E},'
        f'{BOOKINGS}!$H:$H,"No-show")'
    ),

    # ── BY GUIDE (dynamic via QUERY) ─────────────────────────────────────────
    (10, 0): "👤  BY GUIDE — THIS MONTH",

    (11, 0): (
        '=IFERROR(QUERY({'
        f'{BOOKINGS}!D2:D,{BOOKINGS}!H2:H,{BOOKINGS}!K2:K,{BOOKINGS}!B2:B'
        '},'
        '"SELECT Col1, COUNT(Col1), SUM(Col3) '
        'WHERE Col2=\'Yes\' '
        'AND Col4 >= date \'"&TEXT(' + _MOM_S + ',\"yyyy-MM-dd\")&"\' '
        'AND Col4 <= date \'"&TEXT(' + _MOM_E + ',\"yyyy-MM-dd\")&"\' '
        'GROUP BY Col1 '
        'ORDER BY SUM(Col3) DESC '
        'LABEL Col1 \'Guide Name\', COUNT(Col1) \'Confirmed\', SUM(Col3) \'Revenue (QAR)\'"'
        ',0),"No data this month")'
    ),

    # ── ALL TIME ─────────────────────────────────────────────────────────────
    (17, 0): "📈  ALL TIME STATS",

    (18, 0): "TOTAL BOOKINGS",
    (18, 1): "CONFIRMED",
    (18, 2): "NO-SHOWS",
    (18, 3): "TOTAL REVENUE (QAR)",
    (18, 4): "PENDING / UNCONFIRMED",

    (19, 0): f'=COUNTA({BOOKINGS}!$A:$A)-1',
    (19, 1): f'=COUNTIF({BOOKINGS}!$H:$H,"Yes")',
    (19, 2): f'=COUNTIF({BOOKINGS}!$H:$H,"No-show")',
    (19, 3): f'=SUM({BOOKINGS}!$K:$K)',
    (19, 4): (
        f'=COUNTA({BOOKINGS}!$A:$A)-1'
        f'-COUNTIF({BOOKINGS}!$H:$H,"Yes")'
        f'-COUNTIF({BOOKINGS}!$H:$H,"No-show")'
    ),
}


# ── Format request builders ───────────────────────────────────────────────────
def _rng(sheet_id, r1, c1, r2, c2):
    return {"sheetId": sheet_id, "startRowIndex": r1, "endRowIndex": r2,
            "startColumnIndex": c1, "endColumnIndex": c2}


def _fmt(sheet_id, r1, c1, r2, c2, bg=None, fg=BLACK, bold=False,
         size=10, halign="LEFT", valign="MIDDLE", wrap="OVERFLOW_CELL"):
    cell_fmt = {
        "textFormat": {"bold": bold, "fontSize": size, "foregroundColor": fg},
        "horizontalAlignment": halign,
        "verticalAlignment": valign,
        "wrapStrategy": wrap,
    }
    if bg:
        cell_fmt["backgroundColor"] = bg
    return {
        "repeatCell": {
            "range": _rng(sheet_id, r1, c1, r2, c2),
            "cell": {"userEnteredFormat": cell_fmt},
            "fields": "userEnteredFormat",
        }
    }


def _merge(sheet_id, r1, c1, r2, c2):
    return {"mergeCells": {"range": _rng(sheet_id, r1, c1, r2, c2), "mergeType": "MERGE_ALL"}}


def _col_w(sheet_id, c, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def _row_h(sheet_id, r, px):
    return {"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": r, "endIndex": r + 1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def build_format_requests(sheet_id):
    R = []

    # Column widths
    widths = [240, 150, 180, 200, 170, 160]
    for i, w in enumerate(widths):
        R.append(_col_w(sheet_id, i, w))

    # ── Row 0: Title ──────────────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 0, 52))
    R.append(_merge(sheet_id, 0, 0, 1, 4))
    R.append(_fmt(sheet_id, 0, 0, 1, 4, bg=NAVY, fg=WHITE, bold=True, size=14, halign="LEFT", valign="MIDDLE"))
    R.append(_merge(sheet_id, 0, 4, 1, 6))
    R.append(_fmt(sheet_id, 0, 4, 1, 6, bg=NAVY, fg=LIGHT_BLUE, bold=False, size=10, halign="RIGHT", valign="MIDDLE"))

    # ── Row 1: spacer ─────────────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 1, 8))
    R.append(_fmt(sheet_id, 1, 0, 2, 6, bg=NAVY))

    # ── Row 2: TODAY header ───────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 2, 32))
    R.append(_merge(sheet_id, 2, 0, 3, 6))
    R.append(_fmt(sheet_id, 2, 0, 3, 6, bg=DARK_BLUE, fg=WHITE, bold=True, size=11, halign="CENTER"))

    # ── Row 3: TODAY labels ───────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 3, 22))
    for c, bg in enumerate([GREEN, RED, AMBER, DARK_BLUE]):
        R.append(_fmt(sheet_id, 3, c, 4, c + 1, bg=bg, fg=WHITE, bold=True, size=9, halign="CENTER"))
    R.append(_fmt(sheet_id, 3, 3, 4, 6, bg=DARK_BLUE, fg=WHITE, bold=True, size=9, halign="CENTER"))
    R.append(_merge(sheet_id, 3, 3, 4, 6))

    # ── Row 4: TODAY values ───────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 4, 52))
    R.append(_fmt(sheet_id, 4, 0, 5, 1, bg=GREEN, fg=WHITE, bold=True, size=22, halign="CENTER"))
    R.append(_fmt(sheet_id, 4, 1, 5, 2, bg=RED,   fg=WHITE, bold=True, size=22, halign="CENTER"))
    R.append(_fmt(sheet_id, 4, 2, 5, 3, bg=AMBER, fg=WHITE, bold=True, size=22, halign="CENTER"))
    R.append(_fmt(sheet_id, 4, 3, 5, 6, bg=DARK_BLUE, fg=WHITE, bold=True, size=18, halign="CENTER"))
    R.append(_merge(sheet_id, 4, 3, 5, 6))

    # ── Row 5: spacer ─────────────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 5, 12))

    # ── Row 6: THIS MONTH header ──────────────────────────────────────────────
    R.append(_row_h(sheet_id, 6, 32))
    R.append(_merge(sheet_id, 6, 0, 7, 6))
    R.append(_fmt(sheet_id, 6, 0, 7, 6, bg=DARK_BLUE, fg=WHITE, bold=True, size=11, halign="CENTER"))

    # ── Row 7: month labels ───────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 7, 22))
    for c in range(6):
        R.append(_fmt(sheet_id, 7, c, 8, c + 1, bg=LIGHT_BLUE, fg=NAVY, bold=True, size=9, halign="CENTER"))

    # ── Row 8: month values ───────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 8, 48))
    for c in range(6):
        R.append(_fmt(sheet_id, 8, c, 9, c + 1, bg=GREY, fg=NAVY, bold=True, size=16, halign="CENTER"))

    # ── Row 9: spacer ─────────────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 9, 12))

    # ── Row 10: BY GUIDE header ───────────────────────────────────────────────
    R.append(_row_h(sheet_id, 10, 32))
    R.append(_merge(sheet_id, 10, 0, 11, 6))
    R.append(_fmt(sheet_id, 10, 0, 11, 6, bg=DARK_BLUE, fg=WHITE, bold=True, size=11, halign="CENTER"))

    # ── Rows 11-16: guide QUERY output area ───────────────────────────────────
    for r in range(11, 17):
        R.append(_row_h(sheet_id, r, 26))
        R.append(_fmt(sheet_id, r, 0, r + 1, 1, bg=NAVY if r == 11 else WHITE, fg=WHITE if r == 11 else NAVY, bold=(r == 11), size=9 if r == 11 else 10))
        for c in range(1, 3):
            R.append(_fmt(sheet_id, r, c, r + 1, c + 1,
                          bg=DARK_BLUE if r == 11 else GREY,
                          fg=WHITE if r == 11 else NAVY, bold=(r == 11),
                          size=9 if r == 11 else 10, halign="CENTER"))

    # ── Row 16: spacer ────────────────────────────────────────────────────────
    R.append(_row_h(sheet_id, 16, 12))

    # ── Row 17: ALL TIME header ───────────────────────────────────────────────
    R.append(_row_h(sheet_id, 17, 32))
    R.append(_merge(sheet_id, 17, 0, 18, 6))
    R.append(_fmt(sheet_id, 17, 0, 18, 6, bg=DARK_BLUE, fg=WHITE, bold=True, size=11, halign="CENTER"))

    # ── Row 18: all-time labels ───────────────────────────────────────────────
    R.append(_row_h(sheet_id, 18, 22))
    for c in range(5):
        R.append(_fmt(sheet_id, 18, c, 19, c + 1, bg=LIGHT_BLUE, fg=NAVY, bold=True, size=9, halign="CENTER"))

    # ── Row 19: all-time values ───────────────────────────────────────────────
    R.append(_row_h(sheet_id, 19, 48))
    for c in range(5):
        R.append(_fmt(sheet_id, 19, c, 20, c + 1, bg=GREY, fg=NAVY, bold=True, size=16, halign="CENTER"))

    return R


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
    )
    svc = build("sheets", "v4", credentials=creds)
    sid = config.SHEET_ID

    # 1. Get or create Dashboard tab
    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    sheet_id = None
    for sh in meta["sheets"]:
        if sh["properties"]["title"] == DASHBOARD:
            sheet_id = sh["properties"]["sheetId"]
            break

    if sheet_id is None:
        resp = svc.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": DASHBOARD, "index": 0}}}]},
        ).execute()
        sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"Created '{DASHBOARD}' tab  (id={sheet_id})")
    else:
        print(f"Found existing '{DASHBOARD}' tab  (id={sheet_id})")

    # 2. Clear existing content
    svc.spreadsheets().values().clear(
        spreadsheetId=sid, range=f"{DASHBOARD}!A1:Z50"
    ).execute()

    # 3. Write all values / formulas
    rows_data: list[list] = []
    max_row = max(r for r, _ in CELL_VALUES.keys()) + 1
    for r in range(max_row):
        max_col = 6
        row_vals = [""] * max_col
        for (row, col), val in CELL_VALUES.items():
            if row == r and col < max_col:
                row_vals[col] = val
        rows_data.append(row_vals)

    svc.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{DASHBOARD}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows_data},
    ).execute()
    print("Formulas written.")

    # 4. Apply formatting
    fmt_requests = build_format_requests(sheet_id)
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sid, body={"requests": fmt_requests}
    ).execute()
    print("Formatting applied.")

    print(f"\n✅ Dashboard ready — open your Google Sheet and click the '{DASHBOARD}' tab.")


if __name__ == "__main__":
    main()
