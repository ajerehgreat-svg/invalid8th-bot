
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
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):  # 👈 Add this new method
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("Invalid8thBot")

(
    BOOK_NAME, BOOK_DATE, BOOK_LOCATION, BOOK_PACKAGE, BOOK_CONFIRM,
    R2R_AREA, R2R_BUDGET, R2R_STRATEGY, R2R_CONTACT, R2R_CONFIRM
) = range(10)

def main_menu_keyboard():
    buttons = [
        [InlineKeyboardButton("📸 Book a Shoot", callback_data="book_shoot")],
        [InlineKeyboardButton("👑 Membership", callback_data="membership")],
        [InlineKeyboardButton("🏠 R2R / Airbnb Lead", callback_data="r2r")],
        [InlineKeyboardButton("ℹ️ FAQs", callback_data="faqs")],
    ]
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Welcome to Invalid8th Assistant 🤖\n\n"
        "• 📸 Shoot bookings\n"
        "• 👑 Membership info\n"
        "• 🏠 R2R / Airbnb leads\n"
        "• ℹ️ FAQs\n\n"
        "Tap a button to begin."
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard())

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start – menu\n/book – book a shoot\n/r2r – R2R lead\n/membership – membership info\n/help – help"
    )

async def membership_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👑 *Invalid8th Lifestyle Membership*\n"
        "• Basic – £100/mo: priority bookings, 10% off.\n"
        "• Premium – £500/mo: quarterly styled shoot, concierge, 20% off.\n"
        "• Elite – £1,000–1,500/mo: concierge, private dinners, airport bookings, free monthly shoot.\n"
        "_DM @invalid8th to upgrade._"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def faqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ *FAQs*\n"
        "• Shoots: London & nationwide\n"
        "• Turnaround: 48–72h\n"
        "• Payment: 50% deposit\n"
        "• Contact: @invalid8th | ajerehgreat@gmail.com"
    )
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# Booking flow
async def book_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("📸 Your *full name*?", parse_mode="Markdown")
    else:
        await update.message.reply_text("📸 Your *full name*?", parse_mode="Markdown")
    return BOOK_NAME

async def book_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_name"] = update.message.text.strip()
    await update.message.reply_text("Date? *(e.g., 24 Nov 2025)*", parse_mode="Markdown")
    return BOOK_DATE

async def book_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_date"] = update.message.text.strip()
    await update.message.reply_text("Location? *(area or exact address)*", parse_mode="Markdown")
    return BOOK_LOCATION

async def book_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["book_location"] = update.message.text.strip()
    buttons = [
        [InlineKeyboardButton("Lifestyle", callback_data="pkg_lifestyle"),
         InlineKeyboardButton("Concert", callback_data="pkg_concert")],
        [InlineKeyboardButton("Football/Matchday", callback_data="pkg_football"),
         InlineKeyboardButton("Studio", callback_data="pkg_studio")]
    ]
    await update.message.reply_text("Choose *package*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    return BOOK_PACKAGE

async def book_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["book_package"] = q.data.replace("pkg_", "").title()
    summary = (
        f"Confirm booking:\n"
        f"• Name: {context.user_data['book_name']}\n"
        f"• Date: {context.user_data['book_date']}\n"
        f"• Location: {context.user_data['book_location']}\n"
        f"• Package: {context.user_data['book_package']}"
    )
    buttons = [[InlineKeyboardButton("✅ Confirm", callback_data="book_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="book_cancel")]]
    await q.edit_message_text(summary, reply_markup=InlineKeyboardMarkup(buttons))
    return BOOK_CONFIRM

async def book_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data != "book_confirm":
        await q.edit_message_text("Cancelled.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    os.makedirs("data", exist_ok=True)
    from datetime import datetime
    line = f"{datetime.utcnow().isoformat()},{context.user_data.get('book_name')},{context.user_data.get('book_date')},{context.user_data.get('book_location')},{context.user_data.get('book_package')}\n"
    with open("data/bookings.csv", "a", encoding="utf-8") as f:
        f.write(line)
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=f"📸 New booking:\n{line}")
        except Exception as e:
            logger.warning(f"Admin notify failed: {e}")
    await q.edit_message_text("🔥 Booking received! We'll confirm within 24h.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

# R2R flow
async def r2r_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("🏠 Which *area/postcode* is this in?", parse_mode="Markdown")
    return R2R_AREA

async def r2r_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["r2r_area"] = update.message.text.strip()
    await update.message.reply_text("Budget (monthly rent) & est. nightly rate? *(e.g., £2,000 / £130)*", parse_mode="Markdown")
    return R2R_BUDGET

async def r2r_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["r2r_budget"] = update.message.text.strip()
    await update.message.reply_text("Strategy? *(SA whole unit / contractor stays / HMO)*", parse_mode="Markdown")
    return R2R_STRATEGY

async def r2r_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["r2r_strategy"] = update.message.text.strip()
    await update.message.reply_text("Your contact *(email or @handle)*?", parse_mode="Markdown")
    return R2R_CONTACT

async def r2r_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["r2r_contact"] = update.message.text.strip()
    summary = (
        "Confirm R2R lead:\n"
        f"• Area: {context.user_data['r2r_area']}\n"
        f"• Budget/Rates: {context.user_data['r2r_budget']}\n"
        f"• Strategy: {context.user_data['r2r_strategy']}\n"
        f"• Contact: {context.user_data['r2r_contact']}"
    )
    buttons = [[InlineKeyboardButton("✅ Submit", callback_data="r2r_submit"),
                InlineKeyboardButton("❌ Cancel", callback_data="r2r_cancel")]]
    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(buttons))
    return R2R_CONFIRM

async def r2r_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data != "r2r_submit":
        await q.edit_message_text("Cancelled.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END
    os.makedirs("data", exist_ok=True)
    from datetime import datetime
    line = f"{datetime.utcnow().isoformat()},{context.user_data.get('r2r_area')},{context.user_data.get('r2r_budget')},{context.user_data.get('r2r_strategy')},{context.user_data.get('r2r_contact')}\n"
    with open("data/r2r_leads.csv", "a", encoding="utf-8") as f:
        f.write(line)
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=f"🏠 New R2R lead:\n{line}")
        except Exception as e:
            logger.warning(f"Admin notify failed: {e}")
    await q.edit_message_text("✅ Lead submitted. We'll review and get back to you.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    mapping = {
        "book_shoot": book_entry,
        "membership": membership_info,
        "faqs": faqs,
        "r2r": r2r_entry,
    }
    handler = mapping.get(q.data)
    if handler:
        return await handler(update, context)
    await q.answer()
    await q.edit_message_text("Unknown action.", reply_markup=main_menu_keyboard())

def build_app() -> Application:
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_TOKEN env var.")
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("membership", membership_info))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^(book_shoot|membership|faqs|r2r)$"))

    book_conv = ConversationHandler(
        entry_points=[CommandHandler("book", book_entry)],
        states={
            BOOK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_name)],
            BOOK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_date)],
            BOOK_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_location)],
            BOOK_PACKAGE: [CallbackQueryHandler(book_package, pattern="^pkg_")],
            BOOK_CONFIRM: [CallbackQueryHandler(book_confirm, pattern="^book_(confirm|cancel)$")]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app.add_handler(book_conv)

    r2r_conv = ConversationHandler(
        entry_points=[CommandHandler("r2r", r2r_entry)],
        states={
            R2R_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, r2r_area)],
            R2R_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, r2r_budget)],
            R2R_STRATEGY: [MessageHandler(filters.TEXT & ~filters.COMMAND, r2r_strategy)],
            R2R_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, r2r_contact)],
            R2R_CONFIRM: [CallbackQueryHandler(r2r_confirm, pattern="^r2r_(submit|cancel)$")]
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app.add_handler(r2r_conv)
    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
