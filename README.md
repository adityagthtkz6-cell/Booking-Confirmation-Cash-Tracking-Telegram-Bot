# Booking Confirmation & Cash Tracking Telegram Bot

Automated Telegram bot for Sahm Plus Transportation & Services / Discover Arabia.  
Reads daily bookings from Google Sheets, collects confirmation data from guides, and gives cashiers real-time cash queries — all inside a single Telegram group.

---

## Project Structure

```
bot.py                  Entry point
config.py               Environment variable loader
sheets.py               Google Sheets API client
drive.py                Google Drive API client
scheduler.py            Daily booking confirmation job
state.py                In-memory session state (per guide)
handlers/
  booking.py            YES/NO callbacks + 5-step data collection flow
  cashier.py            /cash  /expected  /visitors  /collected
requirements.txt
.env.example
credentials.json        ← you provide this (Google Service Account key)
```

---

## Prerequisites

- Python 3.10 or later
- A Google Cloud project with **Sheets API v4** and **Drive API v3** enabled
- A Google Service Account with a JSON key downloaded
- A Telegram Bot token from [@BotFather](https://t.me/BotFather)
- The bot added to the target Telegram group **with admin rights**

---

## Setup — Step by Step

### 1. Clone / copy files

Place all project files in a single directory.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create the `.env` file

```bash
cp .env.example .env
```

Fill in every value (see the comments inside `.env.example`).

### 4. Configure the Google Service Account

1. Create a service account in Google Cloud Console.
2. Grant it **Editor** access on the target Google Sheet.
3. Share the target Google Drive folder with the service account email.
4. Download the JSON key and save it as `credentials.json` (or whatever path you set in `GOOGLE_CREDENTIALS_FILE`).

### 5. Prepare the Google Sheet

The sheet must have **two tabs** with these exact default names (or override via `.env`):

#### Tab 1 — `Bookings`

BookNetic auto-populates the first seven columns. Add the remaining columns **before running the bot** (the bot will also attempt to add them on first start):

| Column Name | Source |
|---|---|
| Booking number | BookNetic |
| Booking date | BookNetic |
| Tour name | BookNetic |
| Guide name | BookNetic |
| Expected adults | BookNetic |
| Expected kids | BookNetic |
| Expected amount | BookNetic |
| Confirmed | Bot |
| Actual adults | Bot |
| Actual kids | Bot |
| Amount collected | Bot |
| Payment method | Bot |
| Receipt link | Bot |
| Collected by cashier | Bot |

> **Important:** Column names are matched by header text — column order does not matter, but spelling must match exactly (case-sensitive).

#### Tab 2 — `Cash Collections Log`

Create this tab manually with these headers in row 1:

```
Collection date | Guide name | Amount collected | Collected by | Timestamp
```

### 6. Get Telegram User IDs

Ask each user to message [@userinfobot](https://t.me/userinfobot) to get their numeric user ID.  
Set `GUIDE_USER_IDS` and `CASHIER_USER_IDS` in `.env`.

### 7. Get the Telegram Group ID

Add [@RawDataBot](https://t.me/RawDataBot) to the group temporarily — it will show the group's chat ID (a negative number like `-1001234567890`). Set this as `TELEGRAM_GROUP_ID`.

### 8. Run the bot

```bash
python bot.py
```

For production, run it as a background service (systemd, Docker, PM2, etc.) on a VPS.

---

## Bot Flows

### Flow 1 — Daily Booking Confirmation (automatic)

1. Job fires at `TRIGGER_TIME` in `TIMEZONE`.
2. Bot reads all rows where `Booking date` = tomorrow.
3. Posts one inline-keyboard message per booking:

   ```
   📌 Booking #265 | Dhow Cruise Tour | 11 May 2025
   👤 Guide: Ahmed
   Did this booking happen?
   [ ✅ YES ]  [ ❌ NO ]
   ```

4. **Guide taps NO** → row marked `No-show`, row background set to red.
5. **Guide taps YES** → 5-step data collection begins:
   - Adults attended (integer ≥ 0)
   - Kids attended (integer ≥ 0)
   - Amount collected (positive number)
   - Payment method (inline buttons: CASH / BANK TRANSFER)
   - Receipt upload (photo or document)
6. Receipt uploaded to `/Receipts/<Month-YYYY>/` in Google Drive.
7. Row updated with all fields, background set to green.

### Flow 2 — Cashier Commands (on demand)

| Command | Description |
|---|---|
| `/cash [guide name]` | Uncollected cash held by guide |
| `/expected [guide name]` | Expected total from confirmed bookings |
| `/visitors [date]` | Actual vs expected visitors (`today` / `tomorrow` / `DD/MM/YYYY`) |
| `/collected [guide name] [amount]` | Log cash hand-off, reset guide balance to zero |

Only users listed in `CASHIER_USER_IDS` can run these commands.

---

## Access Control

| Role | Configured via | Permitted actions |
|---|---|---|
| Guide | `GUIDE_USER_IDS` | Tap YES/NO, enter booking data, upload receipt |
| Cashier | `CASHIER_USER_IDS` | Run `/cash`, `/expected`, `/visitors`, `/collected` |

Unauthorised users receive: `"You are not authorised to use this command."`

---

## Google Drive Receipt Storage

```
DRIVE_FOLDER_ID/
└── Receipts/
    ├── May-2025/
    │   ├── booking_265_receipt_2025-05-11
    │   └── booking_266_receipt_2025-05-11
    └── Jun-2025/
        └── booking_312_receipt_2025-06-03
```

Files are publicly accessible via a shareable view link stored in the sheet's `Receipt link` column.

---

## Environment Variables Reference

| Variable | Required | Default | Notes |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | From @BotFather |
| `TELEGRAM_GROUP_ID` | ✅ | — | Negative integer |
| `SHEET_ID` | ✅ | — | From sheet URL |
| `DRIVE_FOLDER_ID` | ✅ | — | Parent folder ID |
| `GUIDE_USER_IDS` | ✅ | — | Comma-separated |
| `CASHIER_USER_IDS` | ✅ | — | Comma-separated |
| `GOOGLE_CREDENTIALS_FILE` | ✅ | `credentials.json` | Path to SA key |
| `TRIGGER_TIME` | ✅ | `08:00` | 24h HH:MM |
| `TIMEZONE` | ✅ | `Asia/Qatar` | IANA name |
| `CURRENCY` | — | `QAR` | Display label |
| `BOOKINGS_SHEET_NAME` | — | `Bookings` | Tab name |
| `COLLECTIONS_SHEET_NAME` | — | `Cash Collections Log` | Tab name |

---

## Pre-Launch Checklist

- [ ] Bot token obtained from @BotFather
- [ ] Bot added to group with **admin rights**
- [ ] Google Sheet ID confirmed; BookNetic column names verified
- [ ] Service account created; sheet and Drive folder shared to service account email
- [ ] `credentials.json` in project directory
- [ ] Tab 2 (`Cash Collections Log`) created with correct headers
- [ ] `GUIDE_USER_IDS` and `CASHIER_USER_IDS` populated
- [ ] `TELEGRAM_GROUP_ID` confirmed (negative number)
- [ ] `TRIGGER_TIME` and `TIMEZONE` set
- [ ] `CURRENCY` set to `QAR` (or local currency)
- [ ] Bot starts without errors (`python bot.py`)
- [ ] Test with `/cash` command to verify sheet connectivity
