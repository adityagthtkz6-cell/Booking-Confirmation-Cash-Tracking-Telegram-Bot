import logging
import time as _time
from datetime import date, timedelta, datetime
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y")

_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds


def _retry_on_api_error(func: Callable) -> Callable:
    """Retry Google API calls up to 3 times with exponential backoff."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except HttpError as exc:
                last_exc = exc
                if exc.resp.status in (429, 500, 502, 503):
                    wait = _BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "API error %s on attempt %d/%d for %s — retrying in %ds",
                        exc.resp.status, attempt + 1, _MAX_RETRIES, func.__name__, wait,
                    )
                    _time.sleep(wait)
                else:
                    raise
            except Exception as exc:
                last_exc = exc
                wait = _BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    "Error on attempt %d/%d for %s: %s — retrying in %ds",
                    attempt + 1, _MAX_RETRIES, func.__name__, exc, wait,
                )
                _time.sleep(wait)
        raise last_exc  # type: ignore
    return wrapper

_BOT_COLUMNS = [
    "Confirmed",
    "Actual adults",
    "Actual kids",
    "Amount collected",
    "Payment method",
    "Receipt link",
    "Collected by cashier",
]


def _parse_date(raw: str) -> Optional[date]:
    raw = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


class SheetsClient:
    def __init__(self) -> None:
        self._service = None
        self._col_map: Optional[Dict[str, int]] = None
        self._bookings_sheet_id: Optional[int] = None
        self._collections_sheet_id: Optional[int] = None

    @property
    def service(self):
        if self._service is None:
            creds = service_account.Credentials.from_service_account_file(
                config.GOOGLE_CREDENTIALS_FILE, scopes=_SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def _fetch_sheet_ids(self) -> None:
        if self._bookings_sheet_id is not None:
            return
        meta = self.service.spreadsheets().get(
            spreadsheetId=config.SHEET_ID
        ).execute()
        for sheet in meta.get("sheets", []):
            title = sheet["properties"]["title"]
            sid = sheet["properties"]["sheetId"]
            if title == config.BOOKINGS_SHEET_NAME:
                self._bookings_sheet_id = sid
            elif title == config.COLLECTIONS_SHEET_NAME:
                self._collections_sheet_id = sid

    def _col_map_cached(self) -> Dict[str, int]:
        if self._col_map is not None:
            return self._col_map
        result = self.service.spreadsheets().values().get(
            spreadsheetId=config.SHEET_ID,
            range=f"{config.BOOKINGS_SHEET_NAME}!1:1",
        ).execute()
        headers: list = result.get("values", [[]])[0]
        self._col_map = {str(h).strip(): i for i, h in enumerate(headers)}
        logger.info("Bookings column map loaded: %s", self._col_map)
        return self._col_map

    def invalidate_col_cache(self) -> None:
        self._col_map = None

    @staticmethod
    def _col_letter(zero_based_index: int) -> str:
        result = ""
        n = zero_based_index + 1
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def ensure_bot_columns(self) -> None:
        """Append any missing bot-managed column headers to the Bookings sheet."""
        col_map = self._col_map_cached()
        missing = [c for c in _BOT_COLUMNS if c not in col_map]
        if not missing:
            return
        next_col = max(col_map.values()) + 1 if col_map else 0
        header_row_range = (
            f"{config.BOOKINGS_SHEET_NAME}!"
            f"{self._col_letter(next_col)}1:"
            f"{self._col_letter(next_col + len(missing) - 1)}1"
        )
        self.service.spreadsheets().values().update(
            spreadsheetId=config.SHEET_ID,
            range=header_row_range,
            valueInputOption="RAW",
            body={"values": [missing]},
        ).execute()
        logger.info("Added missing columns to sheet: %s", missing)
        self.invalidate_col_cache()

    def _col(self, col_name: str, row: list, default: Any = "") -> Any:
        idx = self._col_map_cached().get(col_name)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    @_retry_on_api_error
    def _read_all_rows(self) -> List[list]:
        result = self.service.spreadsheets().values().get(
            spreadsheetId=config.SHEET_ID,
            range=f"{config.BOOKINGS_SHEET_NAME}!A:AZ",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
        return result.get("values", [])

    @_retry_on_api_error
    def _write_row(self, row_index: int, updates: Dict[str, Any]) -> None:
        """Read the existing row, patch specific columns, write it back."""
        col_map = self._col_map_cached()
        result = self.service.spreadsheets().values().get(
            spreadsheetId=config.SHEET_ID,
            range=f"{config.BOOKINGS_SHEET_NAME}!{row_index}:{row_index}",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        row: list = result.get("values", [[]])[0] if result.get("values") else []

        needed = max((col_map[k] for k in updates if k in col_map), default=len(row) - 1)
        while len(row) <= needed:
            row.append("")

        for col_name, value in updates.items():
            idx = col_map.get(col_name)
            if idx is not None:
                row[idx] = value
            else:
                logger.warning("Column '%s' not found in sheet — skipping write", col_name)

        self.service.spreadsheets().values().update(
            spreadsheetId=config.SHEET_ID,
            range=f"{config.BOOKINGS_SHEET_NAME}!A{row_index}",
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

    def _write_cell(self, row_index: int, col_name: str, value: Any) -> None:
        col_map = self._col_map_cached()
        idx = col_map.get(col_name)
        if idx is None:
            logger.warning("Column '%s' not found — cannot write cell", col_name)
            return
        cell = f"{config.BOOKINGS_SHEET_NAME}!{self._col_letter(idx)}{row_index}"
        self.service.spreadsheets().values().update(
            spreadsheetId=config.SHEET_ID,
            range=cell,
            valueInputOption="USER_ENTERED",
            body={"values": [[value]]},
        ).execute()

    @_retry_on_api_error
    def _set_row_background(self, row_index: int, rgb: Tuple[float, float, float]) -> None:
        self._fetch_sheet_ids()
        r, g, b = rgb
        requests = [
            {
                "repeatCell": {
                    "range": {
                        "sheetId": self._bookings_sheet_id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": r, "green": g, "blue": b}
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        ]
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=config.SHEET_ID,
            body={"requests": requests},
        ).execute()

    def get_tomorrows_bookings(self) -> List[Dict]:
        tomorrow = date.today() + timedelta(days=1)
        rows = self._read_all_rows()
        if not rows:
            return []

        bookings: List[Dict] = []
        for sheet_row_idx, row in enumerate(rows[1:], start=2):
            raw_date = str(self._col("Booking date", row, "")).strip()
            parsed = _parse_date(raw_date)
            if parsed != tomorrow:
                continue
            bookings.append(
                {
                    "booking_number": str(self._col("Booking number", row, "")),
                    "booking_date": tomorrow.strftime("%d %b %Y"),
                    "tour_name": str(self._col("Tour name", row, "")),
                    "guide_name": str(self._col("Guide name", row, "")),
                    "expected_adults": self._col("Expected adults", row, 0),
                    "expected_kids": self._col("Expected kids", row, 0),
                    "expected_amount": self._col("Expected amount", row, 0),
                    "row_index": sheet_row_idx,
                }
            )
        return bookings

    def find_booking_row(self, booking_number: str) -> Optional[Tuple[list, int]]:
        rows = self._read_all_rows()
        col_map = self._col_map_cached()
        bn_idx = col_map.get("Booking number")
        if bn_idx is None:
            return None
        for sheet_row_idx, row in enumerate(rows[1:], start=2):
            if bn_idx < len(row) and str(row[bn_idx]).strip() == str(booking_number).strip():
                return row, sheet_row_idx
        return None

    def mark_no_show(self, row_index: int) -> None:
        self._write_row(row_index, {
            "Confirmed": "No-show",
            "Expected adults": "",
            "Expected kids": "",
            "Expected amount": "",
            "Actual adults": "",
            "Actual kids": "",
            "Amount collected": "",
            "Payment method": "",
            "Receipt link": "",
        })
        self._set_row_background(row_index, (0.957, 0.400, 0.400))

    def update_booking_confirmed(self, row_index: int, data: Dict) -> None:
        self._write_row(
            row_index,
            {
                "Confirmed": "Yes",
                "Actual adults": data["adults"],
                "Actual kids": data["kids"],
                "Amount collected": data["amount"],
                "Payment method": data["payment_method"],
                "Receipt link": data.get("receipt_link", ""),
            },
        )
        self._set_row_background(row_index, (0.576, 0.769, 0.490))

    def get_guide_cash_balance(self, guide_name: str) -> Tuple[float, int]:
        rows = self._read_all_rows()
        total, count = 0.0, 0
        for row in rows[1:]:
            if not row:
                continue
            if str(self._col("Guide name", row, "")).strip().lower() != guide_name.strip().lower():
                continue
            if str(self._col("Confirmed", row, "")).strip().lower() != "yes":
                continue
            if str(self._col("Payment method", row, "")).strip().lower() != "cash":
                continue
            collected_flag = str(self._col("Collected by cashier", row, "")).strip().lower()
            if collected_flag and collected_flag not in ("", "no"):
                continue
            try:
                total += float(self._col("Amount collected", row, 0) or 0)
                count += 1
            except (ValueError, TypeError):
                pass
        return total, count

    def get_guide_expected_amount(self, guide_name: str) -> Tuple[float, int]:
        rows = self._read_all_rows()
        total, count = 0.0, 0
        for row in rows[1:]:
            if not row:
                continue
            if str(self._col("Guide name", row, "")).strip().lower() != guide_name.strip().lower():
                continue
            if str(self._col("Confirmed", row, "")).strip().lower() != "yes":
                continue
            try:
                total += float(self._col("Expected amount", row, 0) or 0)
                count += 1
            except (ValueError, TypeError):
                pass
        return total, count

    def get_visitors_for_date(self, date_input: str) -> Dict:
        target = _parse_date(date_input)
        if target is None:
            return {"error": f"Cannot parse date: '{date_input}'"}

        rows = self._read_all_rows()
        actual_adults = actual_kids = expected_adults = expected_kids = 0

        for row in rows[1:]:
            if not row:
                continue
            row_dt = _parse_date(str(self._col("Booking date", row, "")))
            if row_dt != target:
                continue
            try:
                expected_adults += int(float(self._col("Expected adults", row, 0) or 0))
                expected_kids += int(float(self._col("Expected kids", row, 0) or 0))
            except (ValueError, TypeError):
                pass
            if str(self._col("Confirmed", row, "")).strip().lower() == "yes":
                try:
                    actual_adults += int(float(self._col("Actual adults", row, 0) or 0))
                    actual_kids += int(float(self._col("Actual kids", row, 0) or 0))
                except (ValueError, TypeError):
                    pass

        return {
            "date_label": target.strftime("%b %d"),
            "actual_adults": actual_adults,
            "actual_kids": actual_kids,
            "actual_total": actual_adults + actual_kids,
            "expected_adults": expected_adults,
            "expected_kids": expected_kids,
            "expected_total": expected_adults + expected_kids,
        }

    @_retry_on_api_error
    def log_cash_collection(
        self,
        guide_name: str,
        amount: float,
        cashier_username: str,
        collection_date: str,
        timestamp: str,
    ) -> None:
        self.service.spreadsheets().values().append(
            spreadsheetId=config.SHEET_ID,
            range=f"{config.COLLECTIONS_SHEET_NAME}!A:E",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[collection_date, guide_name, amount, cashier_username, timestamp]]},
        ).execute()

    def guide_exists(self, guide_name: str) -> bool:
        """Check if any booking row references this guide (case-insensitive)."""
        rows = self._read_all_rows()
        for row in rows[1:]:
            if not row:
                continue
            if str(self._col("Guide name", row, "")).strip().lower() == guide_name.strip().lower():
                return True
        return False

    def mark_bookings_collected(self, guide_name: str) -> None:
        rows = self._read_all_rows()
        col_map = self._col_map_cached()
        collected_idx = col_map.get("Collected by cashier")
        if collected_idx is None:
            logger.warning("'Collected by cashier' column missing — skipping mark-collected step")
            return
        col_letter = self._col_letter(collected_idx)
        for sheet_row_idx, row in enumerate(rows[1:], start=2):
            if not row:
                continue
            if str(self._col("Guide name", row, "")).strip().lower() != guide_name.strip().lower():
                continue
            if str(self._col("Confirmed", row, "")).strip().lower() != "yes":
                continue
            if str(self._col("Payment method", row, "")).strip().lower() != "cash":
                continue
            collected_flag = str(self._col("Collected by cashier", row, "")).strip().lower()
            if collected_flag and collected_flag not in ("", "no"):
                continue
            cell = f"{config.BOOKINGS_SHEET_NAME}!{col_letter}{sheet_row_idx}"
            self.service.spreadsheets().values().update(
                spreadsheetId=config.SHEET_ID,
                range=cell,
                valueInputOption="RAW",
                body={"values": [["Yes"]]},
            ).execute()


    def get_dashboard_stats(self) -> dict:
        """Return today, this-month, and all-time stats for the /dashboard command."""
        rows = self._read_all_rows()
        if not rows:
            return {}

        today = date.today()
        tomorrow = today + timedelta(days=1)
        month_start = today.replace(day=1)
        # month_end covers the full calendar month including upcoming dates
        next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)

        today_confirmed = today_no_show = today_pending = 0
        today_revenue = 0.0
        month_confirmed = month_no_show = 0
        month_revenue = month_cash = month_bank = 0.0
        all_confirmed = all_no_show = all_pending = 0
        all_revenue = 0.0

        for row in rows[1:]:
            if not row:
                continue
            raw_date = str(self._col("Booking date", row, "")).strip()
            booking_date = _parse_date(raw_date)
            confirmed = str(self._col("Confirmed", row, "")).strip()
            amount = float(self._col("Amount collected", row, 0) or 0)
            payment = str(self._col("Payment method", row, "")).strip()

            # All time
            if confirmed == "Yes":
                all_confirmed += 1
                all_revenue += amount
            elif confirmed == "No-show":
                all_no_show += 1
            else:
                all_pending += 1

            # This month (full calendar month, including upcoming confirmed bookings)
            if booking_date and month_start <= booking_date <= month_end:
                if confirmed == "Yes":
                    month_confirmed += 1
                    month_revenue += amount
                    if payment.lower() == "cash":
                        month_cash += amount
                    else:
                        month_bank += amount
                elif confirmed == "No-show":
                    month_no_show += 1

            # Today's snapshot: covers today AND tomorrow
            # (daily confirmations are sent today for tomorrow's tours)
            if booking_date in (today, tomorrow):
                if confirmed == "Yes":
                    today_confirmed += 1
                    today_revenue += amount
                elif confirmed == "No-show":
                    today_no_show += 1
                else:
                    today_pending += 1

        return {
            "today": {
                "confirmed": today_confirmed,
                "no_show": today_no_show,
                "pending": today_pending,
                "revenue": today_revenue,
            },
            "month": {
                "confirmed": month_confirmed,
                "no_show": month_no_show,
                "revenue": month_revenue,
                "cash": month_cash,
                "bank": month_bank,
            },
            "all_time": {
                "confirmed": all_confirmed,
                "no_show": all_no_show,
                "pending": all_pending,
                "revenue": all_revenue,
            },
        }


sheets_client = SheetsClient()
