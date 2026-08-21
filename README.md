# Salon Reminder Bot

A Telegram bot that takes appointment scheduling and reminders off a salon
owner's plate. The owner books appointments through a guided, button-driven
flow instead of a paper diary or a spreadsheet; the bot then automatically
messages each client 24 hours ahead with a one-tap Confirm/Cancel prompt.

## The problem it solves

Small salons lose money to no-shows and lose time to the same few DMs
over and over — "what time is my appointment again?", "can I confirm?",
"I need to cancel." This bot automates that conversation:

- Every appointment gets an automatic reminder 24 hours before it starts.
- Clients confirm or cancel with a single tap, no typing required.
- The owner is notified immediately of every confirmation, cancellation,
  or undo, and is told explicitly when a reminder couldn't be delivered
  (e.g. the client never started the bot).
- The owner manages the whole schedule — adding, rescheduling, editing,
  deleting — through inline buttons, without memorizing commands.

## Features

**Owner navigation**
- Inline main menu (Add appointment / Today / Tomorrow / Upcoming / Settings),
  reachable via `/start` or any "« Back to menu" button.
- A persistent reply keyboard (Add / Today / Tomorrow / Upcoming / Settings)
  pinned below the chat, for one-tap access without opening a menu — owner
  only, clients never see it.
- All owner commands are registered in Telegram's native `/` command list.

**Adding an appointment (`/add`)**
- Client name (typed).
- Service — inline picker (Haircut, Colouring, Manicure, Pedicure, Styling,
  Treatment) or "Other…" to type a custom service.
- Date — quick-pick row for the next 7 days ("Today", "Tomorrow", "Sat 22"…)
  or "Type a date instead" for anything further out.
- Time — a 09:00–20:00 grid in 30-minute steps, or "Type a time instead"
  for anything off-grid.
- Client's Telegram ID (or `0` to save without automatic reminders).
- A summary card on save, with "Add another" / "Done" buttons.

**Viewing the schedule**
- `/today` and `/tomorrow` — a single, column-aligned message per day.
- `/list` (Upcoming) — a single compact table (ID, date, time, client,
  service, status) with a "🔧 Manage appointments" button that opens a
  numbered selector for Edit/Delete on any appointment.
- Edit lets you reschedule (same date/time picker as `/add`), or mark an
  appointment confirmed/cancelled directly.
- Delete asks for confirmation before removing anything.

**Automated reminders**
- A background job checks every 5 minutes for appointments starting within
  24 hours that haven't been reminded yet, aren't cancelled, and have a
  real client contact.
- The client gets a message with Confirm / Cancel buttons.
  - **Confirm** is a single tap, then the message shows "Confirmed" with
    an Undo button (active for 5 minutes).
  - **Cancel** opens a "Cancel this appointment?" step first — the status
    only changes, and the owner is only notified, after "Yes, cancel".
  - **Undo** is also available after a completed cancellation, for the
    same short window, and notifies the owner that the cancellation was
    reversed.
- If a reminder can't be delivered (the client hasn't started the bot),
  it's logged and the owner is notified instead of the job failing silently.

## Screenshots

_TODO: add screenshots of the main menu, the /add flow, the Upcoming list,
and a client-side reminder prompt here._

## Setup

### 1. Clone and enter the project

```bash
git clone <your-repo-url>
cd salon-reminder-bot
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Get a bot token from BotFather

1. Open Telegram and start a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts (choose a name and a unique
   username ending in `bot`).
3. BotFather replies with an API token that looks like
   `123456789:AAExampleTokenxxxxxxxxxxxxxxxxxxxxx`. Copy it.

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

- `BOT_TOKEN` — the token from BotFather.
- `OWNER_ID` — your own numeric Telegram ID (see below).

### 5. Find your Telegram ID (owner and clients)

Both the owner and clients get their numeric Telegram ID the same way:

1. Open a chat with your bot (search for its username, or use the link
   BotFather gave you).
2. Send `/start`.
3. The bot replies with your Telegram ID.

The owner puts that ID in `OWNER_ID` in `.env`. Clients give their ID to
the salon, which the owner enters as the "client's Telegram user ID" step
during `/add` — that's what lets the bot message them directly with
reminders.

### 6. Run the bot

```bash
python bot.py
```

You should see `Bot is running. Press Ctrl+C to stop.` in the terminal.
Message the bot from the `OWNER_ID` account to see the main menu.

## Known limitations

- **Times are stored in local server time.** Appointment times are saved
  and compared using the server's local clock (`datetime.now()`), not UTC.
  If you deploy this on a server set to UTC (or any timezone other than
  the salon's), reminders will fire at the wrong wall-clock time relative
  to the appointment. Keep the host's system timezone matched to the
  salon's, or the reminder window will shift.
- **Clients must start the bot before they can receive messages.** Telegram
  only allows a bot to message a user after that user has opened a chat
  with it (typically by sending `/start`). If a client hasn't done this,
  reminders can't be delivered — the bot detects this, skips the send, and
  notifies the owner instead of crashing, but the client still won't get
  a reminder until they start the bot themselves.
- **Single-salon, single-owner.** There's one `OWNER_ID` with full access
  to every appointment in one shared database. There's no multi-tenant
  support, no per-staff logins, and no concept of multiple salons or
  calendars in the same deployment.
