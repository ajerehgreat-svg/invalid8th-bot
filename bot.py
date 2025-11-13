import os
import logging
from datetime import datetime, date, time, timedelta
# --- tiny web server for Render health checks ---
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):  # healthcheck for HEAD requests too
        self.send_response(200)
        self.end_headers()

def start_healthcheck():
    port = int(os.getenv("PORT", "10000"))  # Render injects PORT automatically
    server = HTTPServer(("0.0.0.0", port), Health)
    server.serve_forever()

# start it on a background thread so it doesn’t block the bot
threading.Thread(target=start_healthcheck, daemon=True).start()
# --- end health server ---

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# convert ADMIN_CHAT_ID to int if present
if ADMIN_CHAT_ID:
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
    except ValueError:
        ADMIN_CHAT_ID = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("Invalid8thBot")

# Conversation states
(
    BOOK_NAME,
    BOOK_DATE,
    BOOK_TIME,
    BOOK_LOCATION,
    BOOK_TYPE,
    LIFESTYLE_HOURS,
    MATCHDAY_PLAYERS,
) = range(7)

# In-memory bookings: {user_id: booking_dict}
BOOKINGS = {}
# Confirmed bookings list for clash checks
CONFIRMED_BOOKINGS = []


# ---------- HELPERS FOR DATE/TIME & CLASH CHECKING ---------- #

def parse_date_str(date_text: str) -> date:
    """
    Accepts formats like:
    - 24 Nov 2025
    - 24 November 2025
    - 24/11/2025
    """
    date_text = date_text.strip()
    fmts = ["%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Unrecognised date format")


def parse_time_str(time_text: str) -> time:
    """
    Accepts 24h '14:30' or '9:05'.
    """
    time_text = time_text.strip()
    try:
        return datetime.strptime(time_text, "%H:%M").time()
    except ValueError:
        raise ValueError("Unrecognised time format")


def check_time_spacing(start_dt: datetime, end_dt: datetime, other_bookings: list):
    """
    Check new booking vs other bookings.
    Returns dict:
      {
        "overlap": bool,
        "close_gap": bool,  # less than 3 hours apart (but not overlapping)
        "nearest": booking_dict or None
      }
    """
    result = {"overlap": False, "close_gap": False, "nearest": None}
    min_gap = None

    for b in other_bookings:
        b_start = b.get("start_dt")
        b_end = b.get("end_dt")
        if not b_start or not b_end:
            continue

        # Overlap: times intersect
        if start_dt < b_end and end_dt > b_start:
            result["overlap"] = True
            # choose the earliest overlapping booking as reference
            if result["nearest"] is None or b_start < result["nearest"]["start_dt"]:
                result["nearest"] = b
            continue

        # If not overlapping, measure closest gap
        # gap is time between end of earlier and start of later
        if end_dt <= b_start:
            gap_seconds = (b_start - end_dt).total_seconds()
        elif start_dt >= b_end:
            gap_seconds = (start_dt - b_end).total_seconds()
        else:
            # shouldn't get here if not overlapping but just in case
            continue

        if min_gap is None or gap_seconds < min_gap:
            min_gap = gap_seconds
            result["nearest"] = b
            # close gap = less than 3 hours apart
            if gap_seconds < 3 * 3600:
                result["close_gap"] = True
            else:
                result["close_gap"] = False

    return result


def save_booking_to_csv(booking: dict):
    """Save finalised booking (with travel fee) to CSV and memory."""
    try:
        os.makedirs("data", exist_ok=True)
        total = booking["base_price"] + (booking.get("travel_fee") or 0)
        line = (
            f"{datetime.utcnow().isoformat()},"
            f"{booking.get('user_id')},"
            f"{booking.get('username')},"
            f"{booking.get('name')},"
            f"{booking.get('date')},"
            f"{booking.get('time')},"
            f"{booking.get('location')},"
            f"{booking.get('type')},"
            f"{booking.get('hours')},"
            f"{booking.get('players')},"
            f"{booking.get('base_price')},"
            f"{booking.get('travel_fee')},"
            f"{total},"
            f"{booking.get('start_dt').isoformat() if booking.get('start_dt') else ''},"
            f"{booking.get('end_dt').isoformat() if booking.get('end_dt') else ''}\n"
        )
        with open("data/bookings.csv", "a", encoding="utf-8") as f:
            f.write(line)

        # also add to confirmed bookings in memory
        CONFIRMED_BOOKINGS.append(booking.copy())
    except Exception as e:
        logger.warning(f"Failed to save booking CSV: {e}")


# ----------------------- UI ELEMENTS ----------------------- #

def main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("📸 Book a Shoot", callback_data="book_shoot")],
        [InlineKeyboardButton("ℹ️ FAQs", callback_data="faqs")],
    ]
    return InlineKeyboardMarkup(buttons)


# ----------------------- BASIC COMMANDS ----------------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Welcome to Invalid8th Assistant 🤖\n\n"
        "• 📸 Lifestyle & Matchday shoot bookings\n"
        "• ℹ️ FAQs\n\n"
        "Tap a button to begin."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start – menu\n"
        "/book – book a lifestyle or matchday shoot\n"
        "/faqs – FAQs\n"
        "/help – this help\n"
        "/travel – (admin only) set travel fee for a booking"
    )


async def faqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ *FAQs*\n"
        "• Shoots: London & nationwide\n"
        "• Turnaround: 48–72h\n"
        "• Lifestyle: £150 for 1h, £100/h for 2h+ (excl. travel)\n"
        "• Matchday: £300 up to 3 players, £100 each for 4+ (excl. travel)\n"
        "• Payment: upfront to secure slot\n"
        "• Contact: @invalid8th | ajerehgreat@gmail.com"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )


# ----------------------- BOOKING FLOW ----------------------- #

async def book_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for booking – ask for name."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📸 Your *full name*?", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("📸 Your *full name*?", parse_mode="Markdown")
    return BOOK_NAME


async def book_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Date? *(e.g., 24 Nov 2025 or 24/11/2025)*", parse_mode="Markdown"
    )
    return BOOK_DATE


async def book_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    date_text = update.message.text.strip()
    try:
        parsed_date = parse_date_str(date_text)
    except ValueError:
        await update.message.reply_text(
            "Please send the date in a format like *24 Nov 2025* or *24/11/2025*.",
            parse_mode="Markdown",
        )
        return BOOK_DATE

    context.user_data["book_date_text"] = date_text
    context.user_data["book_date"] = parsed_date.isoformat()
    await update.message.reply_text(
        "What *time* is the shoot? *(e.g., 14:30)*", parse_mode="Markdown"
    )
    return BOOK_TIME


async def book_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_text = update.message.text.strip()
    try:
        parsed_time = parse_time_str(time_text)
    except ValueError:
        await update.message.reply_text(
            "Please send the time in *24h format*, e.g. *14:30* or *09:00*.",
            parse_mode="Markdown",
        )
        return BOOK_TIME

    context.user_data["book_time_text"] = time_text
    context.user_data["book_time"] = parsed_time.isoformat(timespec="minutes")
    await update.message.reply_text(
        "Location? *(area or exact address)*", parse_mode="Markdown"
    )
    return BOOK_LOCATION


async def book_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_location"] = update.message.text.strip()

    buttons = [
        [
            InlineKeyboardButton("Lifestyle", callback_data="type_lifestyle"),
            InlineKeyboardButton("Matchday", callback_data="type_matchday"),
        ]
    ]
    await update.message.reply_text(
        "What *type of shoot* is this?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return BOOK_TYPE


async def book_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    choice = q.data

    if choice == "type_lifestyle":
        context.user_data["shoot_type"] = "lifestyle"
        await q.edit_message_text(
            "How many *hours* do you want to book?", parse_mode="Markdown"
        )
        return LIFESTYLE_HOURS

    elif choice == "type_matchday":
        context.user_data["shoot_type"] = "matchday"
        await q.edit_message_text(
            "How many *players* from the same team?", parse_mode="Markdown"
        )
        return MATCHDAY_PLAYERS

    await q.edit_message_text("Unknown choice. Please /book again.")
    return ConversationHandler.END


async def lifestyle_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()

    try:
        hours = int(text)
        if hours <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Please send a valid number of hours (e.g. 1, 2, 3)."
        )
        return LIFESTYLE_HOURS

    # Pricing: £150 if <2h (1h), otherwise £100/h
    if hours < 2:
        base_price = 150
    else:
        base_price = hours * 100

    # Build start/end datetimes
    d = date.fromisoformat(context.user_data["book_date"])
    t = time.fromisoformat(context.user_data["book_time"])
    start_dt = datetime.combine(d, t)
    end_dt = start_dt + timedelta(hours=hours)

    booking = {
        "user_id": user_id,
        "username": user.username,
        "name": context.user_data.get("book_name"),
        "date": context.user_data.get("book_date_text"),
        "time": context.user_data.get("book_time_text"),
        "location": context.user_data.get("book_location"),
        "type": "lifestyle",
        "hours": hours,
        "players": None,
        "base_price": base_price,
        "travel_fee": None,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }
    BOOKINGS[user_id] = booking

    # Check clashes against confirmed + pending bookings
    others = list(CONFIRMED_BOOKINGS) + [
        b for uid, b in BOOKINGS.items() if uid != user_id
    ]
    spacing = check_time_spacing(start_dt, end_dt, others)

    conflict_note = ""
    if spacing["overlap"]:
        conflict_note = (
            "\n\n⚠️ _This time clashes with another booking._ "
            "I'll confirm manually and may need to adjust your time."
        )
    elif spacing["close_gap"]:
        conflict_note = (
            "\n\n⚠️ _This is quite close to another booking._ "
            "I'll confirm manually and let you know if timing works."
        )

    await update.message.reply_text(
        "Lifestyle Shoot – Summary\n"
        f"• Name: {booking['name']}\n"
        f"• Date: {booking['date']}\n"
        f"• Time: {booking['time']}\n"
        f"• Location: {booking['location']}\n"
        f"• Hours: {booking['hours']}\n"
        f"• Base shoot fee (no travel): £{booking['base_price']}\n"
        f"{'Travel fee depends on your location.'}"
        "\nI’ll confirm the travel fee and send you the *final total to pay* here."
        f"{conflict_note}",
        parse_mode="Markdown",
    )

    # Notify admin
    if ADMIN_CHAT_ID:
        try:
            clash_text = ""
            if spacing["overlap"]:
                clash_text = "CLASH: overlaps with another booking.\n"
            elif spacing["close_gap"]:
                clash_text = "NOTE: less than 3 hours from another booking.\n"

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "NEW LIFESTYLE BOOKING 🔔\n"
                    f"From: @{user.username or user.full_name} (ID: {user_id})\n"
                    f"Name: {booking['name']}\n"
                    f"Date: {booking['date']}\n"
                    f"Time: {booking['time']}\n"
                    f"Location: {booking['location']}\n"
                    f"Hours: {booking['hours']}\n"
                    f"Base fee (no travel): £{booking['base_price']}\n"
                    f"{clash_text}\n"
                    "Set travel fee with:\n"
                    f"/travel {user_id} <amount>"
                ),
            )
        except Exception as e:
            logger.warning(f"Admin notify failed (lifestyle): {e}")

    return ConversationHandler.END


async def matchday_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()

    try:
        players = int(text)
        if players <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Please send a valid number of players (e.g. 1, 2, 3)."
        )
        return MATCHDAY_PLAYERS

    # Pricing: £300 total for 1–3 players, £100 each for 4+
    if players <= 3:
        base_price = 300
    else:
        base_price = players * 100

    # Assume matchday coverage ~3 hours
    d = date.fromisoformat(context.user_data["book_date"])
    t = time.fromisoformat(context.user_data["book_time"])
    start_dt = datetime.combine(d, t)
    end_dt = start_dt + timedelta(hours=3)

    booking = {
        "user_id": user_id,
        "username": user.username,
        "name": context.user_data.get("book_name"),
        "date": context.user_data.get("book_date_text"),
        "time": context.user_data.get("book_time_text"),
        "location": context.user_data.get("book_location"),
        "type": "matchday",
        "hours": None,
        "players": players,
        "base_price": base_price,
        "travel_fee": None,
        "start_dt": start_dt,
        "end_dt": end_dt,
    }
    BOOKINGS[user_id] = booking

    others = list(CONFIRMED_BOOKINGS) + [
        b for uid, b in BOOKINGS.items() if uid != user_id
    ]
    spacing = check_time_spacing(start_dt, end_dt, others)

    conflict_note = ""
    if spacing["overlap"]:
        conflict_note = (
            "\n\n⚠️ _This time clashes with another booking._ "
            "I'll confirm manually and may need to adjust your time."
        )
    elif spacing["close_gap"]:
        conflict_note = (
            "\n\n⚠️ _This is quite close to another booking._ "
            "I'll confirm manually and let you know if timing works."
        )

    await update.message.reply_text(
        "Matchday Shoot – Summary\n"
        f"• Name: {booking['name']}\n"
        f"• Date: {booking['date']}\n"
        f"• Time: {booking['time']}\n"
        f"• Location: {booking['location']}\n"
        f"• Players: {booking['players']}\n"
        f"• Base shoot fee (no travel): £{booking['base_price']}\n"
        "Travel fee depends on your location.\n"
        "I’ll confirm the travel fee and send you the *final total to pay* here."
        f"{conflict_note}",
        parse_mode="Markdown",
    )

    # Notify admin
    if ADMIN_CHAT_ID:
        try:
            clash_text = ""
            if spacing["overlap"]:
                clash_text = "CLASH: overlaps with another booking.\n"
            elif spacing["close_gap"]:
                clash_text = "NOTE: less than 3 hours from another booking.\n"

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "NEW MATCHDAY BOOKING 🔔\n"
                    f"From: @{user.username or user.full_name} (ID: {user_id})\n"
                    f"Name: {booking['name']}\n"
                    f"Date: {booking['date']}\n"
                    f"Time: {booking['time']}\n"
                    f"Location: {booking['location']}\n"
                    f"Players: {booking['players']}\n"
                    f"Base fee (no travel): £{booking['base_price']}\n"
                    f"{clash_text}\n"
                    "Set travel fee with:\n"
                    f"/travel {user_id} <amount>"
                ),
            )
        except Exception as e:
            logger.warning(f"Admin notify failed (matchday): {e}")

    return ConversationHandler.END


# ----------------------- ADMIN: SET TRAVEL & CONFIRM ----------------------- #

async def set_travel_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: /travel <user_id> <amount>"""
    if ADMIN_CHAT_ID is None:
        await update.message.reply_text("ADMIN_CHAT_ID is not configured.")
        return

    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not allowed to use this command.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Use: /travel <user_id> <amount>")
        return

    try:
        user_id = int(context.args[0])
        travel_fee = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Both user_id and amount must be numbers.")
        return

    booking = BOOKINGS.get(user_id)
    if not booking:
        await update.message.reply_text("No active booking found for that user.")
        return

    booking["travel_fee"] = travel_fee

    # Check against already-confirmed bookings only
    spacing = check_time_spacing(
        booking["start_dt"], booking["end_dt"], CONFIRMED_BOOKINGS
    )
    warning_lines = []
    if spacing["overlap"]:
        warning_lines.append("⚠️ WARNING: This overlaps with an existing confirmed booking.")
    elif spacing["close_gap"]:
        warning_lines.append("ℹ️ Note: This is less than 3 hours from another confirmed booking.")

    total = booking["base_price"] + travel_fee

    # Message to client
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "Final price confirmed ✅\n"
                f"• Shoot fee: £{booking['base_price']}\n"
                f"• Travel: £{travel_fee}\n\n"
                f"**Total to pay: £{total}**\n\n"
                "Please send payment to:\n"
                "Name: YOUR NAME HERE\n"
                "Sort Code: XX-XX-XX\n"
                "Account: XXXXXXXXX\n\n"
                "Your slot is *not* locked in until payment is made.",
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Failed to message client in /travel: {e}")
        await update.message.reply_text("Could not message the client, but fee was set.")

    # Save to CSV + confirmed list
    save_booking_to_csv(booking)

    # Confirm to admin
    warn_text = ("\n".join(warning_lines) + "\n") if warning_lines else ""
    await update.message.reply_text(
        f"{warn_text}"
        f"Travel fee set to £{travel_fee} for user {user_id}. Total: £{total}."
    )


# ----------------------- BUTTON ROUTER (MENU) ----------------------- #

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data == "book_shoot":
        return await book_entry(update, context)
    elif data == "faqs":
        return await faqs(update, context)

    await q.answer()
    await q.edit_message_text("Unknown action.", reply_markup=main_menu_keyboard())


# ----------------------- APP SETUP ----------------------- #

def build_app() -> Application:
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_TOKEN env var.")
    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("faqs", faqs))
    app.add_handler(CommandHandler("travel", set_travel_fee))

    # Main menu buttons
    app.add_handler(CallbackQueryHandler(button_router, pattern="^(book_shoot|faqs)$"))

    # Booking conversation (command + button)
    book_conv = ConversationHandler(
        entry_points=[
            CommandHandler("book", book_entry),
            CallbackQueryHandler(book_entry, pattern="^book_shoot$"),
        ],
        states={
            BOOK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_name)],
            BOOK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_date)],
            BOOK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_time)],
            BOOK_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_location)],
            BOOK_TYPE: [CallbackQueryHandler(book_type, pattern="^type_(lifestyle|matchday)$")],
            LIFESTYLE_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, lifestyle_hours)],
            MATCHDAY_PLAYERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, matchday_players)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(book_conv)

    return app


def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
