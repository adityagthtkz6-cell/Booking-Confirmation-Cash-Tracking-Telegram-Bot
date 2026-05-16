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


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Current chat ID: `{chat_id}`", parse_mode="Markdown")


async def trigger_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
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
    app.add_handler(CommandHandler("trigger", trigger_command))
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
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
