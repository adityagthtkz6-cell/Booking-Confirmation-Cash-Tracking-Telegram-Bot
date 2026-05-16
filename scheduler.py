import logging
from datetime import date, timedelta, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from sheets import sheets_client

logger = logging.getLogger(__name__)


async def send_daily_confirmations(context: ContextTypes.DEFAULT_TYPE, chat_id_override: int = None) -> None:
    """Scheduled job: fetch tomorrow's bookings and post YES/NO prompts to the group."""
    logger.info("Daily booking confirmation job triggered at %s", datetime.now().isoformat())

    chat_id = chat_id_override if chat_id_override is not None else config.TELEGRAM_GROUP_ID
    logger.info("Using chat_id: %s", chat_id)

    try:
        bookings = sheets_client.get_tomorrows_bookings()
    except Exception as exc:
        logger.error("Failed to fetch tomorrow's bookings: %s", exc, exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Failed to fetch tomorrow's bookings. Please check the sheet connection.",
        )
        return

    tomorrow_label = (date.today() + timedelta(days=1)).strftime("%d %b %Y")

    if not bookings:
        logger.info("No bookings found for %s — nothing to post", tomorrow_label)
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📅 *Daily Booking Confirmation — {tomorrow_label}*\n"
            f"{len(bookings)} booking(s) require confirmation:"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    for booking in bookings:
        bn = booking["booking_number"]
        guide_line = f"👤 Guide: {booking['guide_name']}\n" if booking.get("guide_name") else ""
        text = (
            f"📌 *Booking #{bn}* | {booking['tour_name']} | {booking['booking_date']}\n"
            f"{guide_line}"
            f"Did this booking happen?"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ YES", callback_data=f"confirm_{bn}_yes"),
                    InlineKeyboardButton("❌ NO", callback_data=f"confirm_{bn}_no"),
                ]
            ]
        )
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.error("Failed to send prompt for booking %s: %s", bn, exc)
