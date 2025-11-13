import os
import logging
from datetime import datetime
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
    BOOK_LOCATION,
    BOOK_TYPE,
    LIFESTYLE_HOURS,
    MATCHDAY_PLAYERS,
) = range(6)

# In-memory bookings: {user_id: booking_dict}
BOOKINGS = {}


def main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("📸 Book a Shoot", callback_data="book_shoot")],
        [InlineKeyboardButton("ℹ️ FAQs", callback_data="faqs")],
    ]
    return InlineKeyboardMarkup(buttons)


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
        "Date? *(e.g., 24 Nov 2025)*", parse_mode="Markdown"
    )
    return BOOK_DATE


async def book_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_date"] = update.message.text.strip()
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
        await q.edit_message_text("How many *hours* do you want to book?", parse_mode="Markdown")
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

    booking = {
        "user_id": user_id,
        "username": user.username,
        "name": context.user_data.get("book_name"),
        "date": context.user_data.get("book_date"),
        "location": context.user_data.get("book_location"),
        "type": "lifestyle",
        "hours": hours,
        "players": None,
        "base_price": base_price,
        "travel_fee": None,
    }
    BOOKINGS[user_id] = booking

    await update.message.reply_text(
        "Lifestyle Shoot – Summary\n"
        f"• Name: {booking['name']}\n"
        f"• Date: {booking['date']}\n"
        f"• Location: {booking['location']}\n"
        f"• Hours: {booking['hours']}\n"
        f"• Base shoot fee (no travel): £{booking['base_price']}\n\n"
        "Travel fee depends on your location.\n"
        "I’ll confirm the travel fee and send you the *final total to pay* here.",
        parse_mode="Markdown",
    )

    # Notify admin
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "NEW LIFESTYLE BOOKING 🔔\n"
                    f"From: @{user.username or user.full_name} (ID: {user_id})\n"
                    f"Name: {booking['name']}\n"
                    f"Date: {booking['date']}\n"
                    f"Location: {booking['location']}\n"
                    f"Hours: {booking['hours']}\n"
                    f"Base fee (no travel): £{booking['base_price']}\n\n"
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

    booking = {
        "user_id": user_id,
        "username": user.username,
        "name": context.user_data.get("book_name"),
        "date": context.user_data.get("book_date"),
        "location": context.user_data.get("book_location"),
        "type": "matchday",
        "hours": None,
        "players": players,
        "base_price": base_price,
        "travel_fee": None,
    }
    BOOKINGS[user_id] = booking

    await update.message.reply_text(
        "Matchday Shoot – Summary\n"
        f"• Name: {booking['name']}\n"
        f"• Date: {booking['date']}\n"
        f"• Location: {booking['location']}\n"
        f"• Players: {booking['players']}\n"
        f"• Base shoot fee (no travel): £{booking['base_price']}\n\n"
        "Travel fee depends on your location.\n"
        "I’ll confirm the travel fee and send you the *final total to pay* here.",
        parse_mode="Markdown",
    )

    # Notify admin
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "NEW MATCHDAY BOOKING 🔔\n"
                    f"From: @{user.username or user.full_name} (ID: {user_id})\n"
                    f"Name: {booking['name']}\n"
                    f"Date: {booking['date']}\n"
                    f"Location: {booking['location']}\n"
                    f"Players: {booking['players']}\n"
                    f"Base fee (no travel): £{booking['base_price']}\n\n"
                    "Set travel fee with:\n"
                    f"/travel {user_id} <amount>"
                ),
            )
        except Exception as e:
            logger.warning(f"Admin notify failed (matchday): {e}")

    return ConversationHandler.END


def save_booking_to_csv(booking: dict):
    """Save finalised booking (with travel fee) to CSV."""
    try:
        os.makedirs("data", exist_ok=True)
        total = booking["base_price"] + (booking.get("travel_fee") or 0)
        line = (
            f"{datetime.utcnow().isoformat()},"
            f"{booking.get('user_id')},"
            f"{booking.get('username')},"
            f"{booking.get('name')},"
            f"{booking.get('date')},"
            f"{booking.get('location')},"
            f"{booking.get('type')},"
            f"{booking.get('hours')},"
            f"{booking.get('players')},"
            f"{booking.get('base_price')},"
            f"{booking.get('travel_fee')},"
            f"{total}\n"
        )
        with open("data/bookings.csv", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"Failed to save booking CSV: {e}")


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
                "Name: Great Ajereh\n"
                "Sort Code: 04-29-09\n"
                "Account: 91568455\n\n"
                "Your slot is *not* locked in until payment is made.",
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"Failed to message client in /travel: {e}")
        await update.message.reply_text("Could not message the client, but fee was set.")

    # Save to CSV
    save_booking_to_csv(booking)

    # Confirm to admin
    await update.message.reply_text(
        f"Travel fee set to £{travel_fee} for user {user_id}. Total: £{total}."
    )


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

    # FAQs button
    app.add_handler(CallbackQueryHandler(faqs, pattern="^faqs$"))

    # Booking conversation (command + button)
    book_conv = ConversationHandler(
        entry_points=[
            CommandHandler("book", book_entry),
            CallbackQueryHandler(book_entry, pattern="^book_shoot$"),
        ],
        states={
            BOOK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_name)],
            BOOK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_date)],
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
