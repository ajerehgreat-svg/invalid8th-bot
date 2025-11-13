import os
import logging
from datetime import datetime, date, time, timedelta
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- Healthcheck web server for Render ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()


def start_healthcheck():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Health)
    server.serve_forever()


threading.Thread(target=start_healthcheck, daemon=True).start()
# --- end health server ---

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if ADMIN_CHAT_ID:
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
    except ValueError:
        ADMIN_CHAT_ID = None
else:
    ADMIN_CHAT_ID = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("Invalid8thBot")

(
    BOOK_NAME,
    BOOK_IG,
    BOOK_DATE,
    BOOK_TIME,
    BOOK_LOCATION,
    BOOK_TYPE,
    LIFESTYLE_HOURS,
    MATCHDAY_PLAYERS,
) = range(8)

# Pending bookings by user_id
BOOKINGS = {}
# Confirmed bookings list (used for clash checks)
CONFIRMED_BOOKINGS = []


# ---------- HELPERS ---------- #

def parse_date_str(date_text: str) -> date:
    """Parse flexible date formats into a date object."""
    date_text = date_text.strip()
    fmts = ["%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            return datetime.strptime(date_text, fmt).date()
        except ValueError:
            continue
    raise ValueError("Unrecognised date format")


def parse_time_str(time_text: str) -> time:
    """Parse HH:MM 24h time."""
    time_text = time_text.strip()
    return datetime.strptime(time_text, "%H:%M").time()


def check_time_spacing(start_dt: datetime, end_dt: datetime, other_bookings):
    """
    Check overlap and <3h gaps vs other bookings.
    Returns dict: {"overlap": bool, "close_gap": bool, "nearest": booking_or_None}
    """
    result = {"overlap": False, "close_gap": False, "nearest": None}
    min_gap = None

    for b in other_bookings:
        b_start = b.get("start_dt")
        b_end = b.get("end_dt")
        if not b_start or not b_end:
            continue

        # Overlap
        if start_dt < b_end and end_dt > b_start:
            result["overlap"] = True
            if result["nearest"] is None or b_start < result["nearest"]["start_dt"]:
                result["nearest"] = b
            continue

        # Non-overlap gap
        if end_dt <= b_start:
            gap_seconds = (b_start - end_dt).total_seconds()
        elif start_dt >= b_end:
            gap_seconds = (start_dt - b_end).total_seconds()
        else:
            continue

        if min_gap is None or gap_seconds < min_gap:
            min_gap = gap_seconds
            result["nearest"] = b
            result["close_gap"] = gap_seconds < 3 * 3600  # 3 hours

    return result


def save_booking_to_csv(booking: dict):
    """Save confirmed booking and store it in CONFIRMED_BOOKINGS."""
    try:
        os.makedirs("data", exist_ok=True)
        total = booking["base_price"] + (booking.get("travel_fee") or 0)

        line = (
            f"{datetime.utcnow().isoformat()},"
            f"{booking.get('user_id')},"
            f"{booking.get('username')},"
            f"{booking.get('name')},"
            f"{booking.get('instagram')},"
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

        CONFIRMED_BOOKINGS.append(booking.copy())
    except Exception as e:
        logger.warning(f"Failed to save booking CSV: {e}")
def _escape_ics_text(text: str) -> str:
    """Escape text for ICS (commas, semicolons, backslashes, newlines)."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


def _ics_datetime(dt: datetime) -> str:
    """Format datetime as YYYYMMDDTHHMMSS for ICS (floating local time)."""
    return dt.strftime("%Y%m%dT%H%M%S")


def generate_ics_for_booking(booking: dict) -> str:
    """Create ICS content for a single booking."""
    start_dt = booking.get("start_dt")
    end_dt = booking.get("end_dt")

    if not start_dt or not end_dt:
        return ""

    uid = f"{booking.get('user_id')}-{_ics_datetime(start_dt)}@invalid8th"
    dtstamp = _ics_datetime(datetime.utcnow())

    summary = f"Invalid8th {booking.get('type', '').title()} Shoot"
    location = _escape_ics_text(booking.get("location", ""))
    description_lines = [
        f"Name: {booking.get('name')}",
        f"Instagram: {booking.get('instagram')}",
        f"Type: {booking.get('type')}",
    ]
    if booking.get("hours"):
        description_lines.append(f"Hours: {booking.get('hours')}")
    if booking.get("players"):
        description_lines.append(f"Players: {booking.get('players')}")
    total = booking.get("base_price", 0) + (booking.get("travel_fee") or 0)
    description_lines.append(f"Total: £{total}")

    description = _escape_ics_text("\n".join(description_lines))

    ics = (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//Invalid8th//Booking Bot//EN\n"
        "BEGIN:VEVENT\n"
        f"UID:{uid}\n"
        f"DTSTAMP:{dtstamp}\n"
        f"DTSTART:{_ics_datetime(start_dt)}\n"
        f"DTEND:{_ics_datetime(end_dt)}\n"
        f"SUMMARY:{_escape_ics_text(summary)}\n"
        f"LOCATION:{location}\n"
        f"DESCRIPTION:{description}\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
    return ics


def main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("📸 Book a Shoot", callback_data="book_shoot")],
        [InlineKeyboardButton("ℹ️ FAQs", callback_data="faqs")],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------- BASIC COMMANDS ---------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Welcome to the Invalid8th Elite Assistant 🤖\n\n"
        "How to get started:\n"
        "• Tap *📸 Book a Shoot* to book Lifestyle or Matchday content\n"
        "• Tap *ℹ️ FAQs* to see pricing & info\n\n"
        "Key commands:\n"
        "• /start – main menu\n"
        "• /book – start a booking\n"
        "• /faqs – pricing & info\n"
        "• /help – show commands\n"
        "• /export – download bookings (admin only)\n\n"
        "_If you're an Invalid8th member, use your main Instagram handle so we can verify you._"
    )
    if update.message:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "• /start – main menu\n"
        "• /book – book a lifestyle or matchday shoot\n"
        "• /faqs – FAQs & pricing\n"
        "• /travel <user_id> <amount> – set travel fee (admin)\n"
        "• /confirm <user_id> – confirm payment & booking (admin)\n"
        "• /export – download bookings CSV (admin)\n",
        parse_mode="Markdown",
    )


async def faqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ *Invalid8th FAQs*\n\n"
        "*Lifestyle Shoots*\n"
        "• £150 for 1 hour\n"
        "• £100 per hour for 2+ hours\n"
        "(*Travel fee added depending on location*)\n\n"
        "*Matchday Shoots*\n"
        "• £300 total for up to 3 players (same team)\n"
        "• £100 per player for 4+ players (same team)\n"
        "(*Travel fee added depending on location*)\n\n"
        "*General*\n"
        "• Shoots: London & nationwide (UK)\n"
        "• Turnaround: 48–72 hours\n"
        "• Payment: upfront to secure your slot\n"
        "• Contact: @invalid8th | ajerehgreat@gmail.com"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )
    elif update.message:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )


# ---------- BOOKING FLOW ---------- #

async def book_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /book and 📸 button."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📸 Your *full name*?", parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text("📸 Your *full name*?", parse_mode="Markdown")
    return BOOK_NAME


async def book_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_name"] = update.message.text.strip()
    await update.message.reply_text(
        "What’s your *Instagram handle*? (e.g. @invalid8th)\n"
        "_We use this to verify members & keep things secure._",
        parse_mode="Markdown",
    )
    return BOOK_IG


async def book_ig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ig = update.message.text.strip()
    if not ig.startswith("@"):
        ig = "@" + ig
    context.user_data["book_ig"] = ig
    await update.message.reply_text(
        "Date of the shoot? *(e.g., 24 Nov 2025 or 24/11/2025)*",
        parse_mode="Markdown",
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
        "What *time* is the shoot? *(24h format, e.g., 14:30 or 09:00)*",
        parse_mode="Markdown",
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

    if choice == "type_matchday":
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

    # Pricing: £150 if 1h, otherwise £100/h
    if hours < 2:
        base_price = 150
    else:
        base_price = hours * 100

    d = date.fromisoformat(context.user_data["book_date"])
    t = time.fromisoformat(context.user_data["book_time"])
    start_dt = datetime.combine(d, t)
    end_dt = start_dt + timedelta(hours=hours)

    booking = {
        "user_id": user_id,
        "username": user.username,
        "name": context.user_data.get("book_name"),
        "instagram": context.user_data.get("book_ig"),
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
        "status": "pending_travel",
    }
    BOOKINGS[user_id] = booking

    # clash check vs confirmed + other pending
    others = list(CONFIRMED_BOOKINGS) + [
        b for uid, b in BOOKINGS.items() if uid != user_id
    ]
    spacing = check_time_spacing(start_dt, end_dt, others)

    conflict_note = ""
    if spacing["overlap"]:
        conflict_note = (
            "\n\n⚠️ _This time clashes with another booking._ "
            "We'll confirm manually and may need to adjust your time."
        )
    elif spacing["close_gap"]:
        conflict_note = (
            "\n\n⚠️ _This is within 3 hours of another booking._ "
            "We'll confirm manually and let you know if timing works."
        )

    await update.message.reply_text(
        "Lifestyle Shoot – Summary\n"
        f"• Name: {booking['name']}\n"
        f"• Instagram: {booking['instagram']}\n"
        f"• Date: {booking['date']}\n"
        f"• Time: {booking['time']}\n"
        f"• Location: {booking['location']}\n"
        f"• Hours: {booking['hours']}\n"
        f"• Base shoot fee (no travel): £{booking['base_price']}\n\n"
        "Travel fee depends on your location.\n"
        "We’ll confirm the travel fee and send you the *final total to pay* here."
        f"{conflict_note}",
        parse_mode="Markdown",
    )

    # notify admin
    if ADMIN_CHAT_ID:
        try:
            clash_text = ""
            if spacing["overlap"]:
                clash_text = "CLASH: overlaps with another booking.\n"
            elif spacing["close_gap"]:
                clash_text = "NOTE: within 3 hours of another booking.\n"

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "NEW LIFESTYLE BOOKING 🔔\n"
                    f"From: @{user.username or user.full_name} (ID: {user_id})\n"
                    f"Name: {booking['name']}\n"
                    f"Instagram: {booking['instagram']}\n"
                    f"Date: {booking['date']}\n"
                    f"Time: {booking['time']}\n"
                    f"Location: {booking['location']}\n"
                    f"Hours: {booking['hours']}\n"
                    f"Base fee (no travel): £{booking['base_price']}\n"
                    f"{clash_text}"
                    "\nSet travel fee with:\n"
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

    # Pricing: £300 for up to 3 players, else £100/player
    if players <= 3:
        base_price = 300
    else:
        base_price = players * 100

    d = date.fromisoformat(context.user_data["book_date"])
    t = time.fromisoformat(context.user_data["book_time"])
    start_dt = datetime.combine(d, t)
    # Assume matchday block is ~3h
    end_dt = start_dt + timedelta(hours=3)

    booking = {
        "user_id": user_id,
        "username": user.username,
        "name": context.user_data.get("book_name"),
        "instagram": context.user_data.get("book_ig"),
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
        "status": "pending_travel",
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
            "We'll confirm manually and may need to adjust your time."
        )
    elif spacing["close_gap"]:
        conflict_note = (
            "\n\n⚠️ _This is within 3 hours of another booking._ "
            "We'll confirm manually and let you know if timing works."
        )

    await update.message.reply_text(
        "Matchday Shoot – Summary\n"
        f"• Name: {booking['name']}\n"
        f"• Instagram: {booking['instagram']}\n"
        f"• Date: {booking['date']}\n"
        f"• Time: {booking['time']}\n"
        f"• Location: {booking['location']}\n"
        f"• Players: {booking['players']}\n"
        f"• Base shoot fee (no travel): £{booking['base_price']}\n\n"
        "Travel fee depends on your location.\n"
        "We’ll confirm the travel fee and send you the *final total to pay* here."
        f"{conflict_note}",
        parse_mode="Markdown",
    )

    if ADMIN_CHAT_ID:
        try:
            clash_text = ""
            if spacing["overlap"]:
                clash_text = "CLASH: overlaps with another booking.\n"
            elif spacing["close_gap"]:
                clash_text = "NOTE: within 3 hours of another booking.\n"

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "NEW MATCHDAY BOOKING 🔔\n"
                    f"From: @{user.username or user.full_name} (ID: {user_id})\n"
                    f"Name: {booking['name']}\n"
                    f"Instagram: {booking['instagram']}\n"
                    f"Date: {booking['date']}\n"
                    f"Time: {booking['time']}\n"
                    f"Location: {booking['location']}\n"
                    f"Players: {booking['players']}\n"
                    f"Base fee (no travel): £{booking['base_price']}\n"
                    f"{clash_text}"
                    "\nSet travel fee with:\n"
                    f"/travel {user_id} <amount>"
                ),
            )
        except Exception as e:
            logger.warning(f"Admin notify failed (matchday): {e}")

    return ConversationHandler.END


# ---------- ADMIN: TRAVEL + CONFIRM + EXPORT ---------- #

async def set_travel_fee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /travel <user_id> <amount> – set travel, send final price, mark awaiting_payment."""
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
    booking["status"] = "awaiting_payment"
    total = booking["base_price"] + travel_fee

    # Tell client final price + bank details
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "Final price confirmed ✅\n"
                f"• Shoot fee: £{booking['base_price']}\n"
                f"• Travel: £{travel_fee}\n\n"
                f"Total to pay: £{total}\n\n"
                "Please send payment to:\n"
                "Name: GREAT AJEREH\n"
                "Sort Code: 04-29-09\n"
                "Account: 91568455\n\n"
                "Your slot is not locked in until payment is made.\n\n"
                "_Once you’ve paid, send a screenshot of your payment here._"
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Failed to message client in /travel: {e}")
        await update.message.reply_text("Could not message the client, but fee was set.")
        return

    await update.message.reply_text(
        f"Travel fee set to £{travel_fee} for user {user_id}. "
        f"Total they see: £{total}.\n\n"
        "When you’ve confirmed they’ve paid, run:\n"
        f"/confirm {user_id}"
    )


async def confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /confirm <user_id> – mark booking paid & confirmed."""
    if ADMIN_CHAT_ID is None:
        await update.message.reply_text("ADMIN_CHAT_ID is not configured.")
        return

    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not allowed to use this command.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Use: /confirm <user_id>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return

    booking = BOOKINGS.get(user_id)
    if not booking:
        await update.message.reply_text("No active booking found for that user.")
        return

    if booking.get("travel_fee") is None:
        await update.message.reply_text("Travel fee not set yet. Use /travel first.")
        return
    booking["status"] = "confirmed"
    save_booking_to_csv(booking)
    # remove from pending
    del BOOKINGS[user_id]

    total = booking["base_price"] + (booking.get("travel_fee") or 0)

    # --- create ICS file for calendar ---
    ics_content = generate_ics_for_booking(booking)
    ics_path = None
    if ics_content:
        try:
            os.makedirs("data/ics", exist_ok=True)
            ics_filename = f"booking_{booking['user_id']}_{booking['start_dt'].strftime('%Y%m%dT%H%M%S')}.ics"
            ics_path = os.path.join("data", "ics", ics_filename)
            with open(ics_path, "w", encoding="utf-8") as f:
                f.write(ics_content)
        except Exception as e:
            logger.warning(f"Failed to write ICS file: {e}")

    # tell client
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "Payment received – your booking is *CONFIRMED* 🎉\n\n"
                f"Type: {booking['type'].title()} shoot\n"
                f"• Date: {booking['date']}\n"
                f"• Time: {booking['time']}\n"
                f"• Location: {booking['location']}\n"
                f"• Instagram: {booking['instagram']}\n\n"
                "See you there 👌🏾"
            ),
            parse_mode="Markdown",
        )

        # also send ICS to client so they can add to calendar
        if ics_path is not None:
            with open(ics_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename="invalid8th_booking.ics",
                    caption="Tap this to add the booking to your calendar 📅",
                )
    except Exception as e:
        logger.warning(f"Failed to message client in /confirm: {e}")

    # tell admin + send ICS for your iPhone
    await update.message.reply_text(
        "✅ Booking confirmed.\n"
        f"User ID: {user_id}\n"
        f"Name: {booking['name']}\n"
        f"Date: {booking['date']} {booking['time']}\n"
        f"Location: {booking['location']}\n"
        f"Total paid: £{total}"
    )

    if ics_path is not None:
        try:
            with open(ics_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=os.path.basename(ics_path),
                    caption="Tap this to add the booking to your calendar 📅",
                )
        except Exception as e:
            logger.warning(f"Failed to send ICS to admin: {e}")

 

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sends payment screenshot – forward to admin if awaiting_payment."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    msg = update.message

    # ignore admin's own media
    if ADMIN_CHAT_ID is not None and chat_id == ADMIN_CHAT_ID:
        return

    booking = BOOKINGS.get(user.id)

    # only react if awaiting payment
    if not booking or booking.get("status") != "awaiting_payment":
        await msg.reply_text(
            "I can't link this payment to an active booking.\n"
            "If you think this is wrong, message @invalid8th."
        )
        return

    # get image file_id (photo or image document)
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith(
        "image/"
    ):
        file_id = msg.document.file_id

    if not file_id:
        await msg.reply_text("Please send your payment proof as a photo or image file.")
        return

    # forward to admin
    if ADMIN_CHAT_ID:
        total_expected = booking["base_price"] + (booking.get("travel_fee") or 0)
        caption = (
            "💸 *Payment proof received*\n"
            f"User: @{user.username or user.full_name} (ID: {user.id})\n"
            f"Name: {booking['name']}\n"
            f"Type: {booking['type']} | Date: {booking['date']} {booking['time']}\n"
            f"Location: {booking['location']}\n"
            f"Expected total: £{total_expected}\n\n"
            f"Use `/confirm {user.id}` once you've checked your bank."
        )
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=file_id,
            caption=caption,
            parse_mode="Markdown",
        )

    # confirm to user
    await msg.reply_text(
        "Got your payment screenshot ✅\n"
        "Once we’ve checked it, you’ll get a confirmation message here."
    )


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /export – send bookings.csv for Excel."""
    if ADMIN_CHAT_ID is None or update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("You are not allowed to use this command.")
        return

    file_path = "data/bookings.csv"
    if not os.path.exists(file_path):
        await update.message.reply_text("No bookings recorded yet.")
        return

    with open(file_path, "rb") as f:
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=f,
            filename="bookings.csv",
            caption="Here are all confirmed bookings.",
        )


# ---------- APP SETUP ---------- #

def build_app() -> Application:
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_TOKEN env var.")

    app = Application.builder().token(TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("faqs", faqs))
    app.add_handler(CommandHandler("travel", set_travel_fee))
    app.add_handler(CommandHandler("confirm", confirm_payment))
    app.add_handler(CommandHandler("export", export_data))

    # faq button
    app.add_handler(CallbackQueryHandler(faqs, pattern="^faqs$"))

    # booking conversation (handles /book and 📸 button)
    book_conv = ConversationHandler(
        entry_points=[
            CommandHandler("book", book_entry),
            CallbackQueryHandler(book_entry, pattern="^book_shoot$"),
        ],
        states={
            BOOK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_name)],
            BOOK_IG: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_ig)],
            BOOK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_date)],
            BOOK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_time)],
            BOOK_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_location)],
            BOOK_TYPE: [
                CallbackQueryHandler(
                    book_type, pattern="^type_(lifestyle|matchday)$"
                )
            ],
            LIFESTYLE_HOURS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lifestyle_hours)
            ],
            MATCHDAY_PLAYERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, matchday_players)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )
    app.add_handler(book_conv)

    # payment screenshot handler
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE) & ~filters.COMMAND,
            handle_payment_proof,
        )
    )

    return app


def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
