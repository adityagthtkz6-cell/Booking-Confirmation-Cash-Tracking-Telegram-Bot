import os
import tempfile
from dotenv import load_dotenv

load_dotenv()


def _parse_ids(env_var: str) -> list[int]:
    val = os.environ.get(env_var, "")
    if not val.strip():
        return []
    return [int(x.strip()) for x in val.split(",") if x.strip().lstrip("-").isdigit()]


BOT_TOKEN: str = os.environ["BOT_TOKEN"]

TELEGRAM_GROUP_ID: int = int(os.environ["TELEGRAM_GROUP_ID"])

SHEET_ID: str = os.environ["SHEET_ID"]

DRIVE_FOLDER_ID: str = os.environ["DRIVE_FOLDER_ID"]

GUIDE_USER_IDS: list[int] = _parse_ids("GUIDE_USER_IDS")

CASHIER_USER_IDS: list[int] = _parse_ids("CASHIER_USER_IDS")

TRIGGER_TIME: str = os.environ.get("TRIGGER_TIME", "08:00")

TIMEZONE: str = os.environ.get("TIMEZONE", "Asia/Qatar")

CURRENCY: str = os.environ.get("CURRENCY", "QAR")

# Supports either a file path to the JSON key, or the JSON content inline
_creds_raw: str = os.environ.get("GOOGLE_CREDENTIALS_FILE", "") or os.environ.get("GOOGLE_CREDENTIALS_JSON", "credentials.json")

if _creds_raw.strip().startswith("{"):
    # Inline JSON — write to a temp file so google-auth can read it
    _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _tmp.write(_creds_raw)
    _tmp.close()
    GOOGLE_CREDENTIALS_FILE: str = _tmp.name
else:
    GOOGLE_CREDENTIALS_FILE: str = _creds_raw

BOOKINGS_SHEET_NAME: str = os.environ.get("BOOKINGS_SHEET_NAME", "Bookings")

COLLECTIONS_SHEET_NAME: str = os.environ.get("COLLECTIONS_SHEET_NAME", "Cash Collections Log")
