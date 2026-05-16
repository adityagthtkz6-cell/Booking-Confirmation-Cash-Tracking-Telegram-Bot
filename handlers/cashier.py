import logging
from datetime import date, datetime, timedelta

import pytz
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from sheets import sheets_client

logger = logging.getLogger(__name__)

_UNAUTHORIZED = "You are not authorised to use this command."


def _is_cashier(user_id: int) -> bool:
    return user_id in config.CASHIER_USER_IDS


async def cash_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /cash [guide name]
    Returns uncollected cash balance for the given guide.
    """
    if not _is_cashier(update.effective_user.id):
        await update.message.reply_text(_UNAUTHORIZED)
        return

    if not context.args:
        await update.message.reply_text("Usage: `/cash [guide name]`", parse_mode=ParseMode.MARKDOWN)
        return

    guide_name = " ".join(context.args)
    try:
        total, count = sheets_client.get_guide_cash_balance(guide_name)
    except Exception as exc:
        logger.error("cash_command error for '%s': %s", guide_name, exc, exc_info=True)
        await update.message.reply_text("❌ Error fetching data. Please try again.")
        return

    booking_word = "booking" if count == 1 else "bookings"
    await update.message.reply_text(
        f"💰 *{guide_name}* currently holds "
        f"*{config.CURRENCY} {total:,.0f}* in cash ({count} {booking_word}).",
        parse_mode=ParseMode.MARKDOWN,
    )


async def expected_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /expected [guide name]
    Returns expected total from confirmed bookings for the given guide.
    """
    if not _is_cashier(update.effective_user.id):
        await update.message.reply_text(_UNAUTHORIZED)
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/expected [guide name]`", parse_mode=ParseMode.MARKDOWN
        )
        return

    guide_name = " ".join(context.args)
    try:
        total, count = sheets_client.get_guide_expected_amount(guide_name)
    except Exception as exc:
        logger.error("expected_command error for '%s': %s", guide_name, exc, exc_info=True)
        await update.message.reply_text("❌ Error fetching data. Please try again.")
        return

    booking_word = "booking" if count == 1 else "bookings"
    await update.message.reply_text(
        f"📋 Expected total for *{guide_name}*: "
        f"*{config.CURRENCY} {total:,.0f}* across {count} confirmed {booking_word}.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def visitors_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /visitors [date|today|tomorrow|yesterday]
    Returns actual vs expected visitor counts for a date.
    """
    if not _is_cashier(update.effective_user.id):
        await update.message.reply_text(_UNAUTHORIZED)
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/visitors [date]`\n"
            "Date formats: `DD/MM/YYYY`, `YYYY-MM-DD`, `today`, `tomorrow`, `yesterday`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw_input = " ".join(context.args)  # support multi-word dates like '11 May 2025'
    raw_lower = raw_input.lower()
    today = date.today()
    if raw_lower == "today":
        target = today.strftime("%Y-%m-%d")
    elif raw_lower == "tomorrow":
        target = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif raw_lower == "yesterday":
        target = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        target = raw_input

    try:
        stats = sheets_client.get_visitors_for_date(target)
    except Exception as exc:
        logger.error("visitors_command error for '%s': %s", target, exc, exc_info=True)
        await update.message.reply_text("❌ Error fetching data. Please try again.")
        return

    if "error" in stats:
        await update.message.reply_text(f"❌ {stats['error']}")
        return

    await update.message.reply_text(
        f"📊 *{stats['date_label']}*\n"
        f"✅ Actual visitors: *{stats['actual_total']}* "
        f"({stats['actual_adults']} adults, {stats['actual_kids']} kids)\n"
        f"📋 Expected: *{stats['expected_total']}* "
        f"({stats['expected_adults']} adults, {stats['expected_kids']} kids)",
        parse_mode=ParseMode.MARKDOWN,
    )


async def collected_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /collected [guide name] [amount]
    Logs physical cash collection and resets the guide's cash balance.
    """
    if not _is_cashier(update.effective_user.id):
        await update.message.reply_text(_UNAUTHORIZED)
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/collected [guide name] [amount]`\n"
            "Example: `/collected Ahmed Al-Rashidi 1450`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        amount = float(context.args[-1].replace(",", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. The last argument must be a positive number.\n"
            "Example: `/collected Ahmed Al-Rashidi 1450`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    guide_name = " ".join(context.args[:-1])
    user = update.effective_user
    cashier_username = f"@{user.username}" if user.username else f"ID:{user.id}"

    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)
    collection_date = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")

    # Validate guide name exists in sheet
    try:
        if not sheets_client.guide_exists(guide_name):
            await update.message.reply_text(
                "❌ Guide name not found. Please check the spelling and try again."
            )
            return
    except Exception as exc:
        logger.error("guide_exists check failed: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Could not verify guide name. Please try again.")
        return

    try:
        balance_before, count_before = sheets_client.get_guide_cash_balance(guide_name)
    except Exception as exc:
        logger.error("collected_command balance check error: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Could not read guide balance. Please try again.")
        return

    try:
        sheets_client.log_cash_collection(
            guide_name=guide_name,
            amount=amount,
            cashier_username=cashier_username,
            collection_date=collection_date,
            timestamp=timestamp,
        )
    except Exception as exc:
        logger.error("log_cash_collection failed: %s", exc, exc_info=True)
        await update.message.reply_text("❌ Failed to log the collection. Please try again.")
        return

    try:
        sheets_client.mark_bookings_collected(guide_name)
    except Exception as exc:
        logger.error("mark_bookings_collected failed for '%s': %s", guide_name, exc, exc_info=True)
        await update.message.reply_text(
            f"⚠️ Collection logged ({config.CURRENCY} {amount:,.0f}) but failed to mark "
            f"booking rows as collected. Please check the sheet manually.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        f"✅ *Cash collection recorded*\n\n"
        f"👤 Guide: *{guide_name}*\n"
        f"💰 Amount collected: *{config.CURRENCY} {amount:,.0f}*\n"
        f"📅 Date: {collection_date}\n"
        f"🏦 Collected by: {cashier_username}\n\n"
        f"Guide's cash balance has been reset to *{config.CURRENCY} 0*.",
        parse_mode=ParseMode.MARKDOWN,
    )
