import asyncio
import logging
import sys
from datetime import time as dt_time

import pytz
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from handlers.booking import message_handler, payment_callback, yes_no_callback
from handlers.cashier import (
    cash_command,
    collected_command,
    expected_command,
    visitors_command,
)
from scheduler import send_daily_confirmations
from sheets import sheets_client
from state import clear_session, clear_queue


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Current chat ID: `{chat_id}`", parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    clear_session(uid)
    clear_queue(uid)
    await update.message.reply_text("✅ All active booking sessions and queue cleared.")


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_session(user_id)
    if user_id not in config.GUIDE_USER_IDS and user_id not in config.CASHIER_USER_IDS:
        await update.message.reply_text("You are not authorised to use this command.")
        return
    try:
        s = sheets_client.get_dashboard_stats()
    except Exception as exc:
        logger.error("dashboard_command error: %s", exc, exc_info=True)
        await update.message.reply_text("\u274c Error fetching stats. Please try again.")
        return

    t = s.get("today", {})
    m = s.get("month", {})
    a = s.get("all_time", {})

    from datetime import date
    month_name = date.today().strftime("%B %Y")

    text = (
        f"\U0001f4ca *Booking Dashboard*\n"
        f"\n"
        f"\U0001f4c5 *Today's Snapshot*\n"
        f"\u2705 Confirmed: {t.get('confirmed', 0)}\n"
        f"\u274c No-shows: {t.get('no_show', 0)}\n"
        f"\u23f3 Pending: {t.get('pending', 0)}\n"
        f"\U0001f4b0 Revenue: {config.CURRENCY} {t.get('revenue', 0):,.0f}\n"
        f"\n"
        f"\U0001f4c6 *This Month ({month_name})*\n"
        f"\u2705 Confirmed: {m.get('confirmed', 0)}\n"
        f"\u274c No-shows: {m.get('no_show', 0)}\n"
        f"\U0001f4b5 Total Revenue: {config.CURRENCY} {m.get('revenue', 0):,.0f}\n"
        f"\U0001f4b5 Cash: {config.CURRENCY} {m.get('cash', 0):,.0f} | "
        f"\U0001f3e6 Bank: {config.CURRENCY} {m.get('bank', 0):,.0f}\n"
        f"\n"
        f"\U0001f4c8 *All Time*\n"
        f"\u2705 Confirmed: {a.get('confirmed', 0)}\n"
        f"\u274c No-shows: {a.get('no_show', 0)}\n"
        f"\u23f3 Pending: {a.get('pending', 0)}\n"
        f"\U0001f4b0 Total Revenue: {config.CURRENCY} {a.get('revenue', 0):,.0f}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_session(user_id)
    if user_id not in config.GUIDE_USER_IDS and user_id not in config.CASHIER_USER_IDS:
        await update.message.reply_text("You are not authorised to use this command.")
        return
    real_chat_id = update.message.chat_id
    logger.info("trigger_command: message.chat_id=%s  effective_chat.id=%s", real_chat_id, update.effective_chat.id)
    await update.message.reply_text(f"🔄 Manually triggering daily booking confirmations...")
    # Pass the real chat_id directly to scheduler
    await send_daily_confirmations(context, chat_id_override=real_chat_id)

logging.basicConfig(
    format="%(asctime)s  %(name)-24s  %(levelname)-8s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting bot — ensuring required sheet columns exist...")
    try:
        sheets_client.ensure_bot_columns()
    except Exception as exc:
        logger.warning("Could not verify/create sheet columns: %s", exc)

    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("trigger", trigger_command))
    app.add_handler(CommandHandler("dashboard", dashboard_command))
    app.add_handler(CommandHandler("cash", cash_command))
    app.add_handler(CommandHandler("expected", expected_command))
    app.add_handler(CommandHandler("visitors", visitors_command))
    app.add_handler(CommandHandler("collected", collected_command))

    app.add_handler(CallbackQueryHandler(yes_no_callback, pattern=r"^confirm_.+_(yes|no)$"))
    app.add_handler(CallbackQueryHandler(payment_callback, pattern=r"^payment_.+_(cash|bank)$"))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler)
    )
    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, message_handler)
    )

    hour, minute = map(int, config.TRIGGER_TIME.split(":"))
    tz = pytz.timezone(config.TIMEZONE)
    job_time = dt_time(hour, minute, tzinfo=tz)
    app.job_queue.run_daily(
        send_daily_confirmations,
        time=job_time,
        name="daily_booking_confirmations",
    )
    logger.info(
        "Daily confirmation job scheduled at %s (%s)", config.TRIGGER_TIME, config.TIMEZONE
    )

    logger.info("Bot is polling...")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
