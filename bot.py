import os
import re
import html
import logging
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, BotCommand
)
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters, CallbackQueryHandler
)
import database as db

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Conversation steps
NAME, SERVICE, SERVICE_OTHER, DATE_STEP, DATE_OTHER, TIME_STEP, TIME_OTHER, CONTACT = range(8)

SERVICES = ["Haircut", "Colouring", "Manicure", "Pedicure", "Styling", "Treatment"]
DIVIDER = "──────────────────"
UNDO_WINDOW_SECONDS = 300  # how long the Undo button stays on a reminder response

# Persistent reply-keyboard labels (owner only)
BTN_ADD = "➕ Add"
BTN_TODAY = "📅 Today"
BTN_TOMORROW = "📆 Tomorrow"
BTN_UPCOMING = "📋 Upcoming"
BTN_SETTINGS = "⚙️ Settings"
NAV_LABELS = [BTN_TODAY, BTN_TOMORROW, BTN_UPCOMING, BTN_SETTINGS]
NAV_PATTERN = "^(" + "|".join(re.escape(l) for l in NAV_LABELS) + ")$"
ADD_PATTERN = f"^{re.escape(BTN_ADD)}$"
ALL_NAV_PATTERN = "^(" + "|".join(re.escape(l) for l in [BTN_ADD] + NAV_LABELS) + ")$"

# Text-input states must not swallow taps on the persistent reply keyboard.
TEXT_INPUT_FILTER = filters.TEXT & ~filters.COMMAND & ~filters.Regex(ALL_NAV_PATTERN)


def owner_only(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID


def status_mark(status):
    return "✅" if status == "confirmed" else "❌" if status == "cancelled" else "•"


def pad_esc(text, width):
    text = (text or "")[:width].ljust(width)
    return html.escape(text)


# ---------- keyboards ----------

def main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add appointment", callback_data="menu:add")],
        [InlineKeyboardButton("Today", callback_data="menu:today"),
         InlineKeyboardButton("Tomorrow", callback_data="menu:tomorrow")],
        [InlineKeyboardButton("📋 Upcoming", callback_data="menu:upcoming")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings")],
    ])


def owner_reply_keyboard():
    return ReplyKeyboardMarkup(
        [[BTN_ADD, BTN_TODAY, BTN_TOMORROW], [BTN_UPCOMING, BTN_SETTINGS]],
        resize_keyboard=True,
        is_persistent=True,
    )


def back_to_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Back to menu", callback_data="menu:home")]])


def service_picker_keyboard():
    rows, row = [], []
    for s in SERVICES:
        row.append(InlineKeyboardButton(s, callback_data=f"svc:{s}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Other…", callback_data="svc:other")])
    rows.append([InlineKeyboardButton("« Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def date_picker_keyboard():
    today_d = date.today()
    rows, row = [], []
    for i in range(7):
        d = today_d + timedelta(days=i)
        if i == 0:
            label = "Today"
        elif i == 1:
            label = "Tomorrow"
        else:
            label = d.strftime("%a %d")
        row.append(InlineKeyboardButton(label, callback_data=f"date:{d.isoformat()}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Type a date instead", callback_data="date:other")])
    rows.append([InlineKeyboardButton("« Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def time_picker_keyboard():
    slots = []
    cur = datetime.combine(date.today(), time(9, 0))
    end = datetime.combine(date.today(), time(20, 0))
    while cur <= end:
        slots.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=30)
    rows, row = [], []
    for s in slots:
        row.append(InlineKeyboardButton(s, callback_data=f"time:{s}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Type a time instead", callback_data="time:other")])
    rows.append([InlineKeyboardButton("« Back to menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


# ---------- rendering ----------

def render_appointment_card(appt):
    appt_id, name, contact, service, time_str, status = appt
    t = datetime.fromisoformat(time_str)
    text = (
        f"{status_mark(status)} <b>{html.escape(name)}</b> — {html.escape(service or '')}\n"
        f"{t.strftime('%a %d %b, %H:%M')}\n"
        f"<i>#{appt_id} · {status.capitalize()}</i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit", callback_data=f"appt:edit:{appt_id}"),
         InlineKeyboardButton("🗑 Delete", callback_data=f"appt:delete:{appt_id}")],
        [InlineKeyboardButton("« Back to menu", callback_data="menu:home")],
    ])
    return text, markup


def format_day_html(rows, title):
    header = f"<b>{title}</b>\n{DIVIDER}"
    if not rows:
        return f"{header}\nNothing scheduled."
    lines = []
    for row in rows:
        appt_id, name, contact, service, time_str, status = row
        t = datetime.fromisoformat(time_str)
        lines.append(
            f"{status_mark(status)} {t.strftime('%H:%M')}  "
            f"{pad_esc(name, 12)} {pad_esc(service, 10)} #{appt_id}"
        )
    return f"{header}\n<pre>" + "\n".join(lines) + "</pre>"


def format_upcoming_html(rows):
    header = f"<b>Upcoming</b>\n{DIVIDER}"
    if not rows:
        return f"{header}\nNothing scheduled."
    lines = []
    for row in rows:
        appt_id, name, contact, service, time_str, status = row
        t = datetime.fromisoformat(time_str)
        lines.append(
            f"{status_mark(status)} {t.strftime('%d.%m %H:%M')}  "
            f"{pad_esc(name, 12)} {pad_esc(service, 10)} #{appt_id}"
        )
    return f"{header}\n<pre>" + "\n".join(lines) + "</pre>"


def today_text():
    return format_day_html(db.get_appointments_for_day(date.today()), "Today")


def tomorrow_text():
    return format_day_html(db.get_appointments_for_day(date.today() + timedelta(days=1)), "Tomorrow")


def settings_text():
    return (
        f"<b>Settings</b>\n{DIVIDER}\n"
        f"Owner ID: <code>{OWNER_ID}</code>\n"
        "Reminders: checked every 5 minutes, sent 24h before each appointment."
    )


def home_text(update: Update):
    return (
        f"<b>Salon Reminder Bot</b>\n{DIVIDER}\n"
        f"Your Telegram ID: <code>{update.effective_user.id}</code>"
    )


# ---------- home / navigation ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if owner_only(update):
        await update.message.reply_text(
            home_text(update), parse_mode="HTML", reply_markup=main_menu_markup()
        )
        await update.message.reply_text(
            "Quick actions are pinned below.", reply_markup=owner_reply_keyboard()
        )
    else:
        await update.message.reply_text(
            "This bot sends appointment reminders.\n\n"
            f"Your Telegram ID is: {update.effective_user.id}\n"
            "Give this to the salon so they can send you reminders."
        )


async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    if not owner_only(update):
        return ConversationHandler.END
    try:
        await query.edit_message_text(
            home_text(update), parse_mode="HTML", reply_markup=main_menu_markup()
        )
    except TelegramError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=home_text(update), parse_mode="HTML", reply_markup=main_menu_markup()
        )
    return ConversationHandler.END


async def cb_menu_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    await query.edit_message_text(today_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())


async def cb_menu_tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    await query.edit_message_text(tomorrow_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())


async def cb_menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    await query.edit_message_text(settings_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())


async def render_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_upcoming()
    text = format_upcoming_html(rows)
    if rows:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 Manage appointments", callback_data="menu:manage")],
            [InlineKeyboardButton("« Back to menu", callback_data="menu:home")],
        ])
    else:
        markup = back_to_menu_keyboard()

    query = update.callback_query
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def list_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        return
    await render_upcoming(update, context)


async def cb_menu_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    await render_upcoming(update, context)


async def cb_menu_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    rows = db.get_upcoming()
    if not rows:
        await query.edit_message_text("Nothing to manage.", reply_markup=back_to_menu_keyboard())
        return

    kb_rows, row = [], []
    for r in rows:
        appt_id = r[0]
        row.append(InlineKeyboardButton(f"#{appt_id}", callback_data=f"appt:view:{appt_id}"))
        if len(row) == 5:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton("« Back to upcoming", callback_data="menu:upcoming")])
    kb_rows.append([InlineKeyboardButton("« Back to menu", callback_data="menu:home")])

    await query.edit_message_text(
        f"<b>Manage appointments</b>\n{DIVIDER}\nPick one to edit or delete:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows)
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        return
    await update.message.reply_text(today_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        return
    await update.message.reply_text(tomorrow_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delete 3")
        return
    try:
        appt_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("That's not a valid ID.")
        return
    db.delete_appointment(appt_id)
    await update.message.reply_text(f"Deleted #{appt_id}.")


async def reply_keyboard_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes taps on the persistent reply keyboard to the same screens the inline menu uses."""
    if not owner_only(update):
        return ConversationHandler.END
    text = update.message.text.strip()
    context.user_data.clear()
    if text == BTN_TODAY:
        await update.message.reply_text(today_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    elif text == BTN_TOMORROW:
        await update.message.reply_text(tomorrow_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    elif text == BTN_UPCOMING:
        await render_upcoming(update, context)
    elif text == BTN_SETTINGS:
        await update.message.reply_text(settings_text(), parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    return ConversationHandler.END


# ---------- /add + reschedule conversation ----------

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not owner_only(update):
        if update.callback_query:
            await update.callback_query.answer()
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["mode"] = "add"
    text = f"<b>New appointment</b>\n{DIVIDER}\nClient name?"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_to_menu_keyboard()
        )
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    return NAME


async def reschedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return ConversationHandler.END

    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("This appointment no longer exists.")
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data["mode"] = "reschedule"
    context.user_data["appt_id"] = appt_id
    await query.edit_message_text(
        f"<b>Reschedule</b>\n{DIVIDER}\nPick a new date:",
        parse_mode="HTML", reply_markup=date_picker_keyboard()
    )
    return DATE_STEP


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["client_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"<b>Service</b>\n{DIVIDER}", parse_mode="HTML", reply_markup=service_picker_keyboard()
    )
    return SERVICE


async def service_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, value = query.data.split(":", 1)
    if value == "other":
        await query.edit_message_text("Type the service name:", reply_markup=back_to_menu_keyboard())
        return SERVICE_OTHER

    context.user_data["service"] = value
    await query.edit_message_text(
        f"<b>Date</b>\n{DIVIDER}", parse_mode="HTML", reply_markup=date_picker_keyboard()
    )
    return DATE_STEP


async def service_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["service"] = update.message.text.strip()
    await update.message.reply_text(
        f"<b>Date</b>\n{DIVIDER}", parse_mode="HTML", reply_markup=date_picker_keyboard()
    )
    return DATE_STEP


async def date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, value = query.data.split(":", 1)
    if value == "other":
        await query.edit_message_text(
            "Type the date as DD.MM.YYYY\nExample: 25.12.2026",
            reply_markup=back_to_menu_keyboard()
        )
        return DATE_OTHER

    context.user_data["date"] = datetime.strptime(value, "%Y-%m-%d").date()
    await query.edit_message_text(
        f"<b>Time</b>\n{DIVIDER}", parse_mode="HTML", reply_markup=time_picker_keyboard()
    )
    return TIME_STEP


async def date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        d = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await update.message.reply_text(
            "Couldn't read that. Use DD.MM.YYYY\nExample: 25.12.2026",
            reply_markup=back_to_menu_keyboard()
        )
        return DATE_OTHER

    if d < date.today():
        await update.message.reply_text("That's in the past. Try again.", reply_markup=back_to_menu_keyboard())
        return DATE_OTHER

    context.user_data["date"] = d
    await update.message.reply_text(
        f"<b>Time</b>\n{DIVIDER}", parse_mode="HTML", reply_markup=time_picker_keyboard()
    )
    return TIME_STEP


async def finish_time_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, appointment_time):
    context.user_data["appointment_time"] = appointment_time
    query = update.callback_query

    if context.user_data.get("mode") == "reschedule":
        appt_id = context.user_data["appt_id"]
        db.update_appointment_time(appt_id, appointment_time)
        appt = db.get_appointment(appt_id)
        card_text, markup = render_appointment_card(appt)
        text = f"<b>Rescheduled</b>\n{DIVIDER}\n{card_text}"
        if query:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
        else:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)
        context.user_data.clear()
        return ConversationHandler.END

    text = (
        "Client's Telegram user ID? (numbers only)\n\n"
        "The client can get theirs by sending /start to this bot.\n"
        "Send 0 to save without automatic reminders."
    )
    if query:
        await query.edit_message_text(text, reply_markup=back_to_menu_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=back_to_menu_keyboard())
    return CONTACT


async def time_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, value = query.data.split(":", 1)

    if value == "other":
        await query.answer()
        await query.edit_message_text(
            "Type the time as HH:MM (24h)\nExample: 14:30",
            reply_markup=back_to_menu_keyboard()
        )
        return TIME_OTHER

    h, m = map(int, value.split(":"))
    d = context.user_data.get("date")
    if d is None:
        await query.answer("Session expired, please start again.", show_alert=True)
        context.user_data.clear()
        return ConversationHandler.END

    appointment_time = datetime.combine(d, time(h, m))
    if appointment_time < datetime.now():
        await query.answer("That time has already passed.", show_alert=True)
        return TIME_STEP

    await query.answer()
    return await finish_time_selection(update, context, appointment_time)


async def time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await update.message.reply_text(
            "Couldn't read that. Use HH:MM (24h)\nExample: 14:30",
            reply_markup=back_to_menu_keyboard()
        )
        return TIME_OTHER

    d = context.user_data.get("date")
    if d is None:
        await update.message.reply_text(
            "Session expired, please start again.", reply_markup=back_to_menu_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    appointment_time = datetime.combine(d, parsed)
    if appointment_time < datetime.now():
        await update.message.reply_text("That's in the past. Try again.", reply_markup=back_to_menu_keyboard())
        return TIME_OTHER

    return await finish_time_selection(update, context, appointment_time)


async def add_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.text.strip()

    if not contact.lstrip("-").isdigit():
        await update.message.reply_text(
            "That needs to be a numeric Telegram ID, not a username.\n\n"
            "The client gets theirs by sending /start to this bot.\n"
            "Send 0 to save without reminders.",
            reply_markup=back_to_menu_keyboard()
        )
        return CONTACT

    appointment_id = db.add_appointment(
        context.user_data["client_name"],
        contact,
        context.user_data["service"],
        context.user_data["appointment_time"]
    )

    appt = db.get_appointment(appointment_id)
    card_text, _ = render_appointment_card(appt)
    note = "" if contact == "0" else "\n<i>Reminder will be sent 24h before.</i>"
    await update.message.reply_text(
        f"<b>Appointment saved</b>\n{DIVIDER}\n{card_text}{note}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add another", callback_data="add:another")],
            [InlineKeyboardButton("✅ Done", callback_data="add:done")],
        ])
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cb_add_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    await query.edit_message_text(
        home_text(update), parse_mode="HTML", reply_markup=main_menu_markup()
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=back_to_menu_keyboard())
    return ConversationHandler.END


# ---------- appointment card actions ----------

async def cb_appt_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("This appointment no longer exists.")
        return
    text, markup = render_appointment_card(appt)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def cb_appt_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    t = datetime.fromisoformat(time_str)
    text = (
        f"<b>Edit — {html.escape(name)}</b>\n{DIVIDER}\n"
        f"{html.escape(service or '')}\n{t.strftime('%a %d %b, %H:%M')}\n"
        f"Status: {status.capitalize()}"
    )
    buttons = [[InlineKeyboardButton("📅 Reschedule", callback_data=f"appt:reschedule:{appt_id}")]]
    if status != "confirmed":
        buttons.append([InlineKeyboardButton("Mark confirmed", callback_data=f"appt:status:{appt_id}:confirmed")])
    if status != "cancelled":
        buttons.append([InlineKeyboardButton("Mark cancelled", callback_data=f"appt:status:{appt_id}:cancelled")])
    buttons.append([InlineKeyboardButton("« Back", callback_data=f"appt:view:{appt_id}")])
    buttons.append([InlineKeyboardButton("« Back to menu", callback_data="menu:home")])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_appt_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    _, _, appt_id_str, status = query.data.split(":")
    appt_id = int(appt_id_str)
    db.update_status(appt_id, status)
    appt = db.get_appointment(appt_id)
    text, markup = render_appointment_card(appt)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def cb_appt_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    await query.edit_message_text(
        f"Delete the appointment for <b>{html.escape(name)}</b> "
        f"({html.escape(service or '')})?\nThis can't be undone.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Yes, delete", callback_data=f"appt:delete_confirm:{appt_id}"),
            InlineKeyboardButton("Keep it", callback_data=f"appt:delete_cancel:{appt_id}"),
        ]])
    )


async def cb_appt_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    appt_id = int(query.data.split(":")[2])
    db.delete_appointment(appt_id)
    await query.edit_message_text(f"Deleted #{appt_id}.", reply_markup=back_to_menu_keyboard())


async def cb_appt_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not owner_only(update):
        return
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("This appointment no longer exists.")
        return
    text, markup = render_appointment_card(appt)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ---------- reminders ----------

def reminder_keyboard(appointment_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"rem:confirm:{appointment_id}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"rem:cancel_ask:{appointment_id}"),
    ]])


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_appointments_needing_reminder()
    for appt_id, name, contact, service, time_str in rows:
        t = datetime.fromisoformat(time_str)
        when = t.strftime("%d.%m.%Y at %H:%M")
        try:
            await context.bot.send_message(
                chat_id=int(contact),
                text=(
                    f"<b>Reminder</b>\n{DIVIDER}\n"
                    f"{html.escape(service or '')} appointment\n{when}\n\n"
                    "Please confirm or cancel."
                ),
                parse_mode="HTML",
                reply_markup=reminder_keyboard(appt_id)
            )
        except TelegramError as e:
            logger.warning(f"Could not send reminder for #{appt_id} to {contact}: {e}")
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(
                    f"⚠️ Couldn't reach {html.escape(name)} for #{appt_id} "
                    f"({html.escape(service or '')}, {when}).\n"
                    "They may not have started the bot yet."
                ),
                parse_mode="HTML"
            )
        db.mark_reminder_sent(appt_id)


def schedule_undo_expiry(context: ContextTypes.DEFAULT_TYPE, appt_id, chat_id, message_id):
    context.job_queue.run_once(
        _expire_undo, UNDO_WINDOW_SECONDS, data=(appt_id, chat_id, message_id)
    )


async def _expire_undo(context: ContextTypes.DEFAULT_TYPE):
    appt_id, chat_id, message_id = context.job.data
    appt = db.get_appointment(appt_id)
    if not appt or appt[5] == "pending":
        return  # already undone, or changed since — leave the message alone
    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramError:
        pass


async def rem_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.answer()
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    if status != "pending":
        await query.answer("Already updated.", show_alert=True)
        return
    await query.answer()

    db.update_status(appt_id, "confirmed")
    when = datetime.fromisoformat(time_str).strftime("%d.%m.%Y at %H:%M")
    service_esc, name_esc = html.escape(service or ""), html.escape(name)

    await query.edit_message_text(
        f"✅ <b>Confirmed</b>\n{DIVIDER}\n{service_esc} on {when}. See you then!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"rem:undo:{appt_id}")]])
    )
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"✅ {name_esc} confirmed #{appt_id} — {service_esc}, {when}.",
        parse_mode="HTML"
    )
    schedule_undo_expiry(context, appt_id, query.message.chat_id, query.message.message_id)


async def rem_cancel_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.answer()
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    if status != "pending":
        await query.answer("Already updated.", show_alert=True)
        return
    await query.answer()

    when = datetime.fromisoformat(time_str).strftime("%d.%m.%Y at %H:%M")
    await query.edit_message_text(
        f"<b>Cancel this appointment?</b>\n{DIVIDER}\n{html.escape(service or '')} on {when}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, cancel", callback_data=f"rem:cancel_yes:{appt_id}"),
            InlineKeyboardButton("No, go back", callback_data=f"rem:cancel_no:{appt_id}"),
        ]])
    )


async def rem_cancel_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    when = datetime.fromisoformat(time_str).strftime("%d.%m.%Y at %H:%M")
    await query.edit_message_text(
        f"<b>Reminder</b>\n{DIVIDER}\n{html.escape(service or '')} appointment\n{when}\n\n"
        "Please confirm or cancel.",
        parse_mode="HTML",
        reply_markup=reminder_keyboard(appt_id)
    )


async def rem_cancel_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.answer()
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    if status != "pending":
        await query.answer("Already updated.", show_alert=True)
        return
    await query.answer()

    db.update_status(appt_id, "cancelled")
    when = datetime.fromisoformat(time_str).strftime("%d.%m.%Y at %H:%M")
    service_esc, name_esc = html.escape(service or ""), html.escape(name)

    await query.edit_message_text(
        f"❌ <b>Cancelled</b>\n{DIVIDER}\n{service_esc} on {when}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Undo", callback_data=f"rem:undo:{appt_id}")]])
    )
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"❌ CANCELLED: {name_esc} cancelled #{appt_id} — {service_esc}, {when}.",
        parse_mode="HTML"
    )
    schedule_undo_expiry(context, appt_id, query.message.chat_id, query.message.message_id)


async def rem_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    appt_id = int(query.data.split(":")[2])
    appt = db.get_appointment(appt_id)
    if not appt:
        await query.answer()
        await query.edit_message_text("This appointment no longer exists.")
        return

    _, name, contact, service, time_str, status = appt
    if status not in ("confirmed", "cancelled"):
        await query.answer("Nothing to undo.", show_alert=True)
        return
    await query.answer()

    was = status
    db.update_status(appt_id, "pending")
    when = datetime.fromisoformat(time_str).strftime("%d.%m.%Y at %H:%M")
    service_esc, name_esc = html.escape(service or ""), html.escape(name)

    await query.edit_message_text(
        f"<b>Reminder</b>\n{DIVIDER}\n{service_esc} appointment\n{when}\n\n"
        "Please confirm or cancel.",
        parse_mode="HTML",
        reply_markup=reminder_keyboard(appt_id)
    )
    verb = "confirmation" if was == "confirmed" else "cancellation"
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"↩️ {name_esc} undid their {verb} for #{appt_id} — {service_esc}, {when}. Back to pending.",
        parse_mode="HTML"
    )


# ---------- setup ----------

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Open the main menu"),
        BotCommand("add", "Add an appointment"),
        BotCommand("today", "Today's schedule"),
        BotCommand("tomorrow", "Tomorrow's schedule"),
        BotCommand("list", "Upcoming appointments"),
        BotCommand("cancel", "Abort current action"),
    ])


def main():
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(add_start, pattern="^menu:add$"),
            CallbackQueryHandler(add_start, pattern="^add:another$"),
            CallbackQueryHandler(reschedule_start, pattern=r"^appt:reschedule:\d+$"),
            MessageHandler(filters.Regex(ADD_PATTERN), add_start),
        ],
        states={
            NAME: [MessageHandler(TEXT_INPUT_FILTER, add_name)],
            SERVICE: [CallbackQueryHandler(service_chosen, pattern=r"^svc:")],
            SERVICE_OTHER: [MessageHandler(TEXT_INPUT_FILTER, service_typed)],
            DATE_STEP: [CallbackQueryHandler(date_chosen, pattern=r"^date:")],
            DATE_OTHER: [MessageHandler(TEXT_INPUT_FILTER, date_typed)],
            TIME_STEP: [CallbackQueryHandler(time_chosen, pattern=r"^time:")],
            TIME_OTHER: [MessageHandler(TEXT_INPUT_FILTER, time_typed)],
            CONTACT: [MessageHandler(TEXT_INPUT_FILTER, add_contact)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(show_home, pattern="^menu:home$"),
            MessageHandler(filters.Regex(ADD_PATTERN), add_start),
            MessageHandler(filters.Regex(NAV_PATTERN), reply_keyboard_dispatch),
        ],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("list", list_upcoming))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(MessageHandler(filters.Regex(NAV_PATTERN), reply_keyboard_dispatch))

    app.add_handler(CallbackQueryHandler(show_home, pattern="^menu:home$"))
    app.add_handler(CallbackQueryHandler(cb_menu_today, pattern="^menu:today$"))
    app.add_handler(CallbackQueryHandler(cb_menu_tomorrow, pattern="^menu:tomorrow$"))
    app.add_handler(CallbackQueryHandler(cb_menu_upcoming, pattern="^menu:upcoming$"))
    app.add_handler(CallbackQueryHandler(cb_menu_manage, pattern="^menu:manage$"))
    app.add_handler(CallbackQueryHandler(cb_menu_settings, pattern="^menu:settings$"))
    app.add_handler(CallbackQueryHandler(cb_add_done, pattern="^add:done$"))

    app.add_handler(CallbackQueryHandler(cb_appt_view, pattern=r"^appt:view:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_appt_edit, pattern=r"^appt:edit:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_appt_status, pattern=r"^appt:status:\d+:(confirmed|cancelled)$"))
    app.add_handler(CallbackQueryHandler(cb_appt_delete, pattern=r"^appt:delete:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_appt_delete_confirm, pattern=r"^appt:delete_confirm:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_appt_delete_cancel, pattern=r"^appt:delete_cancel:\d+$"))

    app.add_handler(CallbackQueryHandler(rem_confirm, pattern=r"^rem:confirm:\d+$"))
    app.add_handler(CallbackQueryHandler(rem_cancel_ask, pattern=r"^rem:cancel_ask:\d+$"))
    app.add_handler(CallbackQueryHandler(rem_cancel_yes, pattern=r"^rem:cancel_yes:\d+$"))
    app.add_handler(CallbackQueryHandler(rem_cancel_no, pattern=r"^rem:cancel_no:\d+$"))
    app.add_handler(CallbackQueryHandler(rem_undo, pattern=r"^rem:undo:\d+$"))

    app.job_queue.run_repeating(check_reminders, interval=300, first=10)

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
