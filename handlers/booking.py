import logging
import re
from datetime import date, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from drive import drive_client
from sheets import sheets_client
from state import (
    BookingSession, Step,
    clear_session, get_session, set_session,
    enqueue_session, dequeue_next, clear_queue, queue_size,
    is_booking_active_or_queued,
)

logger = logging.getLogger(__name__)

_DATE_FORMATS = ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%b %d %Y")


def _is_guide(user_id: int) -> bool:
    return user_id in config.GUIDE_USER_IDS


async def _start_next_session(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pop the next queued booking for this user and start data collection."""
    next_session = dequeue_next(user_id)
    if next_session is None:
        return
    set_session(user_id, next_session)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"➡️ *Now collecting: Booking #{next_session.booking_number}*"
            f" | {next_session.tour_name}\n\n"
            f"👥 How many *adults* attended?"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


def _parse_booking_date(raw: str) -> date:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return date.today()


async def yes_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not _is_guide(user_id):
        await query.answer("You are not authorised to use this command.", show_alert=True)
        return

    match = re.match(r"^confirm_(.+)_(yes|no)$", query.data)
    if not match:
        return
    booking_number, action = match.group(1), match.group(2)

    result = sheets_client.find_booking_row(booking_number)
    if result is None:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ Booking #{booking_number} was not found in the sheet.",
        )
        return
    row_data, row_index = result

    tour_name = str(sheets_client._col("Tour name", row_data, ""))
    booking_date_raw = str(sheets_client._col("Booking date", row_data, ""))

    # Skip if already confirmed or marked no-show in the sheet
    confirmed_status = str(sheets_client._col("Confirmed", row_data, "")).strip()
    if confirmed_status in ("Yes", "No-show"):
        await query.answer(f"Booking #{booking_number} already processed.", show_alert=True)
        return

    if action == "no":
        try:
            sheets_client.mark_no_show(row_index)
        except Exception as exc:
            logger.error("mark_no_show failed for #%s: %s", booking_number, exc, exc_info=True)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Failed to update sheet for Booking #{booking_number}. Please try again.",
            )
            return
        await query.edit_message_text(
            text=f"🔴 *Booking #{booking_number}* | {tour_name}\nMarked as *No-show*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    booking_date_label = _parse_booking_date(booking_date_raw).strftime("%d %b %Y")
    new_session = BookingSession(
        booking_number=booking_number,
        tour_name=tour_name,
        booking_date=booking_date_raw,
        row_index=row_index,
        step=Step.ADULTS,
    )

    # Prevent duplicate: skip if already active or in queue
    if is_booking_active_or_queued(user_id, booking_number):
        await query.answer(f"Booking #{booking_number} is already being processed.", show_alert=True)
        return

    existing = get_session(user_id)
    if existing is not None:
        # Queue this booking — will auto-start after current one completes
        enqueue_session(user_id, new_session)
        q = queue_size(user_id)
        await query.edit_message_text(
            text=(
                f"✅ *Booking #{booking_number}* | {tour_name} | {booking_date_label}\n"
                f"Confirmed — queued (#{q} in line, will start automatically)."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # No active session — start immediately
    set_session(user_id, new_session)
    await query.edit_message_text(
        text=f"✅ *Booking #{booking_number}* | {tour_name} | {booking_date_label}\nConfirmed — collecting data...",
        parse_mode=ParseMode.MARKDOWN,
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"📋 *Booking #{booking_number}*\n👥 How many *adults* attended?",
        parse_mode=ParseMode.MARKDOWN,
    )


async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not _is_guide(user_id):
        await query.answer("You are not authorised to use this command.", show_alert=True)
        return

    session = get_session(user_id)
    if session is None:
        await query.answer("No active booking session.", show_alert=True)
        return
    if session.step != Step.PAYMENT:
        await query.answer("Unexpected input at this step.", show_alert=True)
        return

    match = re.match(r"^payment_(.+)_(cash|bank)$", query.data)
    if not match:
        return
    booking_number, method_key = match.group(1), match.group(2)

    if booking_number != session.booking_number:
        await query.answer("This button belongs to a different booking.", show_alert=True)
        return

    session.payment_method = "Cash" if method_key == "cash" else "Bank transfer"
    session.step = Step.RECEIPT
    try:
        sheets_client.update_booking_partial(session.row_index, {"Payment method": session.payment_method})
    except Exception:
        pass

    await query.edit_message_text(
        text=f"💳 Payment method: *{session.payment_method}*",
        parse_mode=ParseMode.MARKDOWN,
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"📋 *Booking #{session.booking_number}* — "
            f"Please upload the *receipt or invoice* (photo or document file)."
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route text / photo / document messages into the active booking session."""
    if not update.message:
        return

    user_id = update.effective_user.id
    if not _is_guide(user_id):
        return

    session = get_session(user_id)
    if session is None:
        return

    msg = update.message

    if session.step == Step.ADULTS:
        text = (msg.text or "").strip()
        try:
            adults = int(text)
            if adults < 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Please enter a valid non-negative whole number for adults.")
            return
        session.adults = adults
        session.step = Step.KIDS
        try:
            sheets_client.update_booking_partial(session.row_index, {"Actual adults": adults})
        except Exception:
            pass
        await msg.reply_text(
            f"✅ Adults: *{adults}*\n\n"
            f"📋 *Booking #{session.booking_number}* — How many *kids* attended?",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif session.step == Step.KIDS:
        text = (msg.text or "").strip()
        try:
            kids = int(text)
            if kids < 0:
                raise ValueError
        except ValueError:
            await msg.reply_text("❌ Please enter a valid non-negative whole number for kids.")
            return
        session.kids = kids
        session.step = Step.AMOUNT
        try:
            sheets_client.update_booking_partial(session.row_index, {"Actual kids": kids})
        except Exception:
            pass
        await msg.reply_text(
            f"✅ Kids: *{kids}*\n\n"
            f"📋 *Booking #{session.booking_number}* — "
            f"How much was collected? (enter amount in {config.CURRENCY})",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif session.step == Step.AMOUNT:
        text = (msg.text or "").strip().replace(",", "")
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await msg.reply_text(
                f"❌ Please enter a valid positive number for the amount collected (e.g. 1450)."
            )
            return
        session.amount = amount
        session.step = Step.PAYMENT
        try:
            sheets_client.update_booking_partial(session.row_index, {"Amount collected": amount})
        except Exception:
            pass
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💵 CASH", callback_data=f"payment_{session.booking_number}_cash"
                    ),
                    InlineKeyboardButton(
                        "🏦 BANK TRANSFER",
                        callback_data=f"payment_{session.booking_number}_bank",
                    ),
                ]
            ]
        )
        await msg.reply_text(
            f"✅ Amount: *{config.CURRENCY} {amount:,.2f}*\n\n"
            f"📋 *Booking #{session.booking_number}* — Payment method?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )

    elif session.step == Step.PAYMENT:
        await msg.reply_text(
            "Please use the *buttons above* to select a payment method.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif session.step == Step.RECEIPT:
        if msg.text and not msg.photo and not msg.document:
            await msg.reply_text(
                f"📋 *Booking #{session.booking_number}* — "
                f"Please send a *photo* or *document file* as the receipt.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        file_id: str | None = None
        mime_type = "image/jpeg"

        if msg.photo:
            file_id = msg.photo[-1].file_id
        elif msg.document:
            file_id = msg.document.file_id
            mime_type = msg.document.mime_type or "application/octet-stream"

        if file_id is None:
            await msg.reply_text("❌ Could not read the file. Please send a photo or document.")
            return

        status_msg = await msg.reply_text("⏳ Saving receipt...")

        receipt_link = f"Telegram file_id: {file_id}"

        try:
            sheets_client.update_booking_confirmed(
                row_index=session.row_index,
                data={
                    "adults": session.adults,
                    "kids": session.kids,
                    "amount": session.amount,
                    "payment_method": session.payment_method,
                    "receipt_link": receipt_link,
                },
            )
        except Exception as exc:
            logger.error(
                "Sheet update failed for booking #%s: %s",
                session.booking_number, exc, exc_info=True,
            )
            await status_msg.edit_text(
                f"⚠️ Receipt uploaded but *sheet update failed*.\n"
                f"Receipt link: {receipt_link}\n"
                f"Please update the sheet manually.",
                parse_mode=ParseMode.MARKDOWN,
            )
            clear_session(user_id)
            await _start_next_session(user_id, msg.chat_id, context)
            return

        await status_msg.edit_text(
            f"✅ *Booking #{session.booking_number} — Complete!*\n\n"
            f"👥 Adults: {session.adults} | Kids: {session.kids}\n"
            f"💰 Amount: {config.CURRENCY} {session.amount:,.2f}\n"
            f"💳 Payment: {session.payment_method}\n"
            f"🧾 Receipt: [View on Drive]({receipt_link})\n\n"
            f"Sheet updated ✅  Row coloured green.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        clear_session(user_id)
        await _start_next_session(user_id, msg.chat_id, context)
