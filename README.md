# Google Calendar Telegram Bot

A Telegram bot that sends notifications for your Google Calendar events.

## Features

- 🆕 **Event Notifications**: Get notified when events are created or modified
- ⏰ **Smart Reminders**: Customizable reminders before events (default: 15min, 1hr before)
- 📅 **Daily Summary**: Morning digest of your day's schedule (7 AM default)
- 🔄 **Polling-based**: Checks for changes every 3 minutes (configurable)
- 💾 **State Tracking**: Never get duplicate notifications

## Setup

### 1. Prerequisites

- Python 3.10 or higher
- A Telegram account
- A Google account with Calendar

### 2. Create a Telegram Bot

1. Talk to [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` and follow instructions
3. Copy the bot token (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

### 3. Set Up Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the **Google Calendar API**:
   - Navigate to "APIs & Services" > "Library"
   - Search for "Google Calendar API"
   - Click "Enable"
4. Create OAuth 2.0 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Application type: "Desktop app"
   - Name it anything (e.g., "Calendar Bot")
   - Download the JSON or copy Client ID and Client Secret

### 4. Configure the Bot

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env
```

Fill in:

- `TELEGRAM_BOT_TOKEN`: Your bot token from BotFather
- `GOOGLE_CLIENT_ID`: From Google Cloud Console
- `GOOGLE_CLIENT_SECRET`: From Google Cloud Console

### 5. Install Dependencies

```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 6. Run the Bot

```bash
python bot.py
```

## Usage

### First Time Setup

1. Start a chat with your bot on Telegram
2. Send `/start`
3. Click the Google authorization link
4. Grant calendar read access
5. Copy the authorization code
6. Send the code to the bot

You're all set! The bot will now monitor your calendar.

### Commands

- `/start` - Connect your Google Calendar
- `/summary` - Show today's events on demand
- `/reminders` - View current reminder settings
- `/reminders_default` - Reset to default reminders (15min, 1hr)
- `/help` - Show help message

### Customizing Reminder Times

After `/reminders`, send comma-separated minutes:

```
15,60,1440
```

This sets reminders for:

- 15 minutes before events
- 1 hour (60 min) before events
- 1 day (1440 min) before events

## Configuration Options

Edit `.env` to customize:

- `POLL_INTERVAL_SECONDS`: How often to check for changes (default: 180 = 3 minutes)
- `DAILY_SUMMARY_TIME`: When to send daily summary (default: "07:00")
- `LOG_LEVEL`: Logging verbosity (DEBUG, INFO, WARNING, ERROR)

## Deployment

### Docker (Recommended)

```bash
# Build image
docker build -t calendar-bot .

# Run container
docker run -d \
  --name calendar-bot \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  calendar-bot
```

### Systemd Service (Linux)

Create `/etc/systemd/system/calendar-bot.service`:

```ini
[Unit]
Description=Google Calendar Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/gootenot
Environment=PATH=/path/to/gootenot/venv/bin
ExecStart=/path/to/gootenot/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable calendar-bot
sudo systemctl start calendar-bot
```

## Architecture

```
┌─────────────┐
│  Telegram   │
│   Users     │
└──────┬──────┘
       │
       ▼
┌─────────────────────────┐
│   bot.py                │
│   - Command handlers    │
│   - User interaction    │
└───────┬─────────────────┘
        │
        ▼
┌──────────────────────────┐
│   scheduler.py           │
│   - Polling logic        │
│   - Event processing     │
│   - Notification sending │
└───────┬──────────────────┘
        │
        ├──────────────────────┐
        ▼                      ▼
┌─────────────────┐    ┌──────────────┐
│ calendar_service│    │  database.py │
│ - Google API    │    │  - SQLite    │
│ - OAuth flow    │    │  - State     │
└─────────────────┘    └──────────────┘
```

## Data Storage

All data is stored in `./data/` (configurable):

- `events.db`: SQLite database with user credentials and notification state
- `credentials/`: (Reserved for future file-based credential storage)

## Troubleshooting

### Bot doesn't respond

- Check bot token is correct in `.env`
- Ensure bot.py is running: `ps aux | grep bot.py`
- Check logs for errors

### Authentication fails

- Verify Google Client ID/Secret are correct
- Make sure Google Calendar API is enabled in Cloud Console
- Try creating new OAuth credentials

### Not receiving notifications

- Check `/summary` works (tests calendar connection)
- Verify polling is running (check logs for "Starting calendar poll")
- Ensure events are in your primary calendar
- Check reminder times with `/reminders`

### "Invalid authorization code" error

- Make sure you copied the entire code from Google
- Code expires after a few minutes - be quick or restart with `/start`
- Try pasting code without extra spaces/newlines

## Security Notes

- **Never commit `.env`** with real credentials
- Bot needs only `calendar.readonly` scope (safe, read-only access)
- Credentials are stored encrypted by Google's library
- Each user's credentials are isolated in the database

## License

MIT License - feel free to modify and use however you want!
