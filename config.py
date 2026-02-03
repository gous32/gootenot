"""Configuration management for the calendar bot."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN must be set in .env file")

# Google Calendar API Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")

# Required Google Calendar scopes
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Database Configuration
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "events.db"
CREDENTIALS_DIR = DATA_DIR / "credentials"
CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

# Polling Configuration
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "180"))  # 3 minutes default

# Reminder Configuration
DEFAULT_REMINDER_TIMES = [15, 60]  # Minutes before event (15min, 1hour)

# Daily Summary Configuration
DAILY_SUMMARY_TIME = os.getenv("DAILY_SUMMARY_TIME", "07:00")  # 7 AM default

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
