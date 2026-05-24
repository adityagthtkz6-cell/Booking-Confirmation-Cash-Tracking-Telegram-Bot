"""
booknetic_sync.py
Reads approved appointments from the Booknetic Bookings Google Sheet,
groups them by date+tour/service, and writes new rows to the bot's Bookings sheet.
"""
import logging
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from google.oauth2 import service_account
from googleapiclient.discovery import build

import config
from sheets import sheets_client

logger = logging.getLogger(__name__)

BOOKNETIC_SHEET_ID = "1TJ03MHcBXqQWw8JRfSGPOUy1nrUhiYfXKRRy927SR5w"
BOOKINGS_DATA_TAB = "Bookings Data"
GUIDE_LIST_TAB = "Guide List"


def _booknetic_service():
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds)


def _parse_amount(amount_str: str) -> float:
    """Parse 'QAR 1 800.00' or 'QAR 360.00' to float."""
    if not amount_str:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", str(amount_str).replace("\u00a0", "").replace(" ", ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_extra_list(extra_list: str) -> Tuple[str, int, int]:
    """
    Parse service name, adults count, kids count from Booknetic app_extra_list.
    Examples:
      'Mangrove Kayaking  For Adults x2 - QAR 360.00'         → ('Mangrove Kayaking', 2, 0)
      'Mangrove Kayaking  For Adults - QAR 180.00'             → ('Mangrove Kayaking', 1, 0)
      '3 Day Advantage Package x5 - QAR 4 550.00'             → ('3 Day Advantage Package', 5, 0)
      'Panda House Qatar Tour (Adult) x2 - QAR 360.00'        → ('Panda House Qatar Tour', 2, 0)
    Returns (service_name, adults, kids)
    """
    if not extra_list:
        return ("Unknown Tour", 1, 0)

    main_part = extra_list.split("<br/>")[0].strip().rstrip(",").strip()

    adults = 0
    kids = 0

    adults_x = re.search(r"For\s+Adults\s+x(\d+)", main_part, re.IGNORECASE)
    kids_x = re.search(r"For\s+Kids\s+x(\d+)", main_part, re.IGNORECASE)
    single_adult = re.search(r"For\s+Adults(?!\s+x)", main_part, re.IGNORECASE)
    single_kid = re.search(r"For\s+Kids(?!\s+x)", main_part, re.IGNORECASE)

    if adults_x:
        adults = int(adults_x.group(1))
    elif single_adult:
        adults = 1

    if kids_x:
        kids = int(kids_x.group(1))
    elif single_kid:
        kids = 1

    service = "Unknown Tour"
    m = re.match(r"^(.+?)\s+For\s+(?:Adults|Kids)", main_part, re.IGNORECASE)
    if m:
        service = m.group(1).strip()
    else:
        m2 = re.match(r"^(.+?)\s+x(\d+)\s+-", main_part, re.IGNORECASE)
        if m2:
            service = m2.group(1).strip()
            if adults == 0:
                adults = int(m2.group(2))
        else:
            m3 = re.match(r"^(.+?)\s+-\s+QAR", main_part, re.IGNORECASE)
            if m3:
                service = m3.group(1).strip()

    if adults == 0 and kids == 0:
        adults = 1

    return service, adults, kids


def _get_guide_list() -> List[Dict]:
    """Read Guide Name → Chat ID mapping from the Guide List tab."""
    svc = _booknetic_service()
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=BOOKNETIC_SHEET_ID,
            range=f"{GUIDE_LIST_TAB}!A2:B100",
        ).execute()
        rows = result.get("values", [])
        guides = []
        for r in rows:
            if len(r) >= 2:
                try:
                    guides.append({"name": r[0].strip(), "chat_id": int(r[1])})
                except (ValueError, IndexError):
                    pass
        return guides
    except Exception as exc:
        logger.error("Error reading Guide List tab: %s", exc)
        return []


def _next_booking_number() -> str:
    """Generate next booking number in BK-YYYY-NNN format."""
    year = date.today().year
    try:
        existing = sheets_client.get_all_booking_numbers()
        max_n = 0
        for bn in existing:
            m = re.match(rf"^BK-{year}-(\d+)$", str(bn))
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"BK-{year}-{max_n + 1:03d}"
    except Exception:
        return f"BK-{year}-001"


def sync_tomorrow_to_bot_sheet() -> int:
    """
    Fetch tomorrow's approved Booknetic appointments, group by tour/service,
    and append new rows to the bot's Bookings sheet.
    Returns number of new booking rows added.
    """
    tomorrow = date.today() + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")

    svc = _booknetic_service()
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=BOOKNETIC_SHEET_ID,
            range=f"{BOOKINGS_DATA_TAB}!A1:M5000",
        ).execute()
    except Exception as exc:
        logger.error("Failed to read Booknetic Bookings sheet: %s", exc)
        return 0

    rows = result.get("values", [])
    if len(rows) < 2:
        logger.info("Booknetic sheet is empty or has no data rows")
        return 0

    headers = rows[0]

    def col(row: list, name: str, default: str = "") -> str:
        try:
            idx = headers.index(name)
            return row[idx] if idx < len(row) else default
        except ValueError:
            return default

    guides = _get_guide_list()
    default_guide = guides[0]["name"] if guides else "Supervisor"

    grouped: Dict[str, Dict] = {}

    for row in rows[1:]:
        appt_date = col(row, "Appointment Date", "")
        if not appt_date.startswith(tomorrow_str):
            continue

        status = col(row, "appointment_status", "").strip().lower()
        if status != "approved":
            continue

        service = col(row, "service_name", "").strip() or _parse_extra_list(col(row, "app_extra_list", ""))[0]
        amount_str = col(row, "app_sum_price", "")
        amount = _parse_amount(amount_str)

        try:
            adults = int(col(row, "Adult_Cnt", "0") or 0)
            kids = int(col(row, "Kids_Cnt", "0") or 0)
        except ValueError:
            _, adults, kids = _parse_extra_list(col(row, "app_extra_list", ""))

        if adults == 0 and kids == 0:
            _, adults, kids = _parse_extra_list(col(row, "app_extra_list", ""))

        appt_id = col(row, "Appointment ID", "")
        customer_name = col(row, "Customer Name", "")

        if service not in grouped:
            grouped[service] = {
                "service": service,
                "adults": 0, "kids": 0, "amount": 0.0,
                "ticket_ids": [], "customer_names": [],
            }

        grouped[service]["adults"] += adults
        grouped[service]["kids"] += kids
        grouped[service]["amount"] += amount
        if appt_id:
            grouped[service]["ticket_ids"].append(str(appt_id))
        if customer_name:
            grouped[service]["customer_names"].append(customer_name)

    if not grouped:
        logger.info("No approved Booknetic bookings found for %s", tomorrow_str)
        return 0

    existing_bookings = sheets_client.get_tomorrows_bookings()
    existing_tours = {b["tour_name"].strip().lower() for b in existing_bookings}

    added = 0
    for service, data in grouped.items():
        if service.strip().lower() in existing_tours:
            logger.info("Booking for '%s' on %s already exists — skipping", service, tomorrow_str)
            continue

        bn = _next_booking_number()
        new_row = {
            "Booking number": bn,
            "Booking date": tomorrow_str,
            "Tour name": service,
            "Guide name": default_guide,
            "Expected adults": data["adults"],
            "Expected kids": data["kids"],
            "Expected amount": round(data["amount"], 2),
            "Ticket IDs": ", ".join(data["ticket_ids"]),
            "Customer Names": ", ".join(dict.fromkeys(data["customer_names"])),
        }

        try:
            sheets_client.append_booking_row(new_row)
            logger.info(
                "Synced booking %s: %s | %d adults, %d kids, QAR %.0f | tickets: %s",
                bn, service, data["adults"], data["kids"], data["amount"],
                ", ".join(data["ticket_ids"]),
            )
            added += 1
        except Exception as exc:
            logger.error("Failed to write booking %s to bot sheet: %s", bn, exc)

    return added


def sync_all_to_bot_sheet(days_back: int = 30) -> int:
    """
    Full sync: pull ALL approved Booknetic bookings (from days_back days ago
    through all future dates), group by date+tour, and add missing rows to the
    bot's Bookings sheet.
    Returns total number of new rows added.
    """
    cutoff = date.today() - timedelta(days=days_back)

    svc = _booknetic_service()
    try:
        result = svc.spreadsheets().values().get(
            spreadsheetId=BOOKNETIC_SHEET_ID,
            range=f"{BOOKINGS_DATA_TAB}!A1:M5000",
        ).execute()
    except Exception as exc:
        logger.error("Failed to read Booknetic sheet for full sync: %s", exc)
        return 0

    rows = result.get("values", [])
    if len(rows) < 2:
        return 0

    headers = rows[0]

    def col(row: list, name: str, default: str = "") -> str:
        try:
            idx = headers.index(name)
            return row[idx] if idx < len(row) else default
        except ValueError:
            return default

    guides = _get_guide_list()
    default_guide = guides[0]["name"] if guides else "Supervisor"

    grouped: Dict[str, Dict] = {}

    for row in rows[1:]:
        appt_date_str = col(row, "Appointment Date", "")
        if not appt_date_str:
            continue
        try:
            appt_date = date.fromisoformat(appt_date_str[:10])
        except ValueError:
            continue
        if appt_date < cutoff:
            continue

        status = col(row, "appointment_status", "").strip().lower()
        if status != "approved":
            continue

        amount_str = col(row, "app_sum_price", "")
        service = col(row, "service_name", "").strip() or _parse_extra_list(col(row, "app_extra_list", ""))[0]
        amount = _parse_amount(amount_str)

        try:
            adults = int(col(row, "Adult_Cnt", "0") or 0)
            kids = int(col(row, "Kids_Cnt", "0") or 0)
        except ValueError:
            _, adults, kids = _parse_extra_list(col(row, "app_extra_list", ""))
        if adults == 0 and kids == 0:
            _, adults, kids = _parse_extra_list(col(row, "app_extra_list", ""))

        appt_id = col(row, "Appointment ID", "")
        customer_name = col(row, "Customer Name", "")

        key = f"{appt_date_str[:10]}||{service}"
        if key not in grouped:
            grouped[key] = {
                "service": service,
                "date": appt_date_str[:10],
                "adults": 0, "kids": 0, "amount": 0.0,
                "ticket_ids": [], "customer_names": [],
            }
        grouped[key]["adults"] += adults
        grouped[key]["kids"] += kids
        grouped[key]["amount"] += amount
        if appt_id:
            grouped[key]["ticket_ids"].append(str(appt_id))
        if customer_name:
            grouped[key]["customer_names"].append(customer_name)

    if not grouped:
        logger.info("No approved Booknetic bookings found for full sync")
        return 0

    existing_rows = sheets_client._read_all_rows()
    existing_keys = set()
    if existing_rows:
        col_map = sheets_client._col_map_cached()
        bn_idx = col_map.get("Booking date")
        tn_idx = col_map.get("Tour name")
        for r in existing_rows[1:]:
            d = str(r[bn_idx]).strip() if bn_idx is not None and bn_idx < len(r) else ""
            t = str(r[tn_idx]).strip().lower() if tn_idx is not None and tn_idx < len(r) else ""
            if d and t:
                existing_keys.add(f"{d}||{t}")

    added = 0
    for key, data in sorted(grouped.items()):
        date_str = data["date"]
        service = data["service"]
        lookup_key = f"{date_str}||{service.strip().lower()}"
        if lookup_key in existing_keys:
            logger.info("Already exists: %s — %s", date_str, service)
            continue

        bn = _next_booking_number()
        new_row = {
            "Booking number": bn,
            "Booking date": date_str,
            "Tour name": service,
            "Guide name": default_guide,
            "Expected adults": data["adults"],
            "Expected kids": data["kids"],
            "Expected amount": round(data["amount"], 2),
            "Ticket IDs": ", ".join(data["ticket_ids"]),
            "Customer Names": ", ".join(dict.fromkeys(data["customer_names"])),
        }
        try:
            sheets_client.append_booking_row(new_row)
            existing_keys.add(lookup_key)
            logger.info("Full sync added %s: %s on %s | tickets: %s", bn, service, date_str, ", ".join(data["ticket_ids"]))
            added += 1
        except Exception as exc:
            logger.error("Full sync failed for %s on %s: %s", service, date_str, exc)

    return added
