"""Database management for tracking events and user state."""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import logging

import config

logger = logging.getLogger(__name__)


class Database:
    """Manages SQLite database for event tracking and user data."""

    def __init__(self, db_path: Path = config.DATABASE_PATH):
        self.db_path = db_path
        self.init_database()

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_database(self):
        """Initialize database schema."""
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY,
                    google_credentials TEXT,
                    reminder_times TEXT,  -- JSON array of minutes before event
                    daily_summary_enabled INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_poll_time TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS notified_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    event_id TEXT,
                    calendar_id TEXT,
                    last_modified TEXT,
                    notification_type TEXT,  -- created, modified, reminder_15, reminder_60, etc
                    notified_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(chat_id, event_id, notification_type)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_notified_events_lookup
                ON notified_events(chat_id, event_id, notification_type)
            """)

            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")

    # User Management

    def add_user(self, chat_id: int) -> bool:
        """Add a new user or return False if already exists."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "INSERT INTO users (chat_id) VALUES (?)",
                    (chat_id,)
                )
                conn.commit()
                logger.info(f"Added new user: {chat_id}")
                return True
        except sqlite3.IntegrityError:
            return False

    def save_user_credentials(self, chat_id: int, credentials_json: str):
        """Save Google OAuth credentials for user."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE users SET google_credentials = ? WHERE chat_id = ?",
                (credentials_json, chat_id)
            )
            conn.commit()
            logger.info(f"Saved credentials for user {chat_id}")

    def get_user_credentials(self, chat_id: int) -> Optional[str]:
        """Get stored Google credentials for user."""
        with self.get_connection() as conn:
            result = conn.execute(
                "SELECT google_credentials FROM users WHERE chat_id = ?",
                (chat_id,)
            ).fetchone()
            return result["google_credentials"] if result else None

    def set_reminder_times(self, chat_id: int, minutes: List[int]):
        """Set reminder times (in minutes before event) for user."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE users SET reminder_times = ? WHERE chat_id = ?",
                (json.dumps(minutes), chat_id)
            )
            conn.commit()

    def get_reminder_times(self, chat_id: int) -> List[int]:
        """Get user's reminder times or default."""
        with self.get_connection() as conn:
            result = conn.execute(
                "SELECT reminder_times FROM users WHERE chat_id = ?",
                (chat_id,)
            ).fetchone()

            if result and result["reminder_times"]:
                return json.loads(result["reminder_times"])
            return config.DEFAULT_REMINDER_TIMES

    def get_all_users(self) -> List[int]:
        """Get all registered user chat IDs."""
        with self.get_connection() as conn:
            results = conn.execute("SELECT chat_id FROM users").fetchall()
            return [row["chat_id"] for row in results]

    def get_last_poll_time(self, chat_id: int) -> Optional[datetime]:
        """Get last poll time for user."""
        with self.get_connection() as conn:
            result = conn.execute(
                "SELECT last_poll_time FROM users WHERE chat_id = ?",
                (chat_id,)
            ).fetchone()

            if result and result["last_poll_time"]:
                return datetime.fromisoformat(result["last_poll_time"])
            return None

    def update_last_poll_time(self, chat_id: int, poll_time: datetime):
        """Update last poll time for user."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE users SET last_poll_time = ? WHERE chat_id = ?",
                (poll_time.isoformat(), chat_id)
            )
            conn.commit()

    # Event Notification Tracking

    def has_notification_sent(
        self,
        chat_id: int,
        event_id: str,
        notification_type: str
    ) -> bool:
        """Check if a specific notification has been sent for an event."""
        with self.get_connection() as conn:
            result = conn.execute(
                """SELECT 1 FROM notified_events
                   WHERE chat_id = ? AND event_id = ? AND notification_type = ?""",
                (chat_id, event_id, notification_type)
            ).fetchone()
            return result is not None

    def mark_notification_sent(
        self,
        chat_id: int,
        event_id: str,
        calendar_id: str,
        last_modified: str,
        notification_type: str
    ):
        """Mark that a notification has been sent."""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO notified_events
                   (chat_id, event_id, calendar_id, last_modified, notification_type)
                   VALUES (?, ?, ?, ?, ?)""",
                (chat_id, event_id, calendar_id, last_modified, notification_type)
            )
            conn.commit()

    def cleanup_old_notifications(self, days: int = 30):
        """Remove old notification records."""
        with self.get_connection() as conn:
            conn.execute(
                """DELETE FROM notified_events
                   WHERE notified_at < datetime('now', '-' || ? || ' days')""",
                (days,)
            )
            conn.commit()
            logger.info(f"Cleaned up notifications older than {days} days")

    def clear_user_data(self, chat_id: int) -> bool:
        """Clear all data for a user (credentials, notifications, settings)."""
        try:
            with self.get_connection() as conn:
                # Delete notification history
                conn.execute(
                    "DELETE FROM notified_events WHERE chat_id = ?",
                    (chat_id,)
                )
                # Delete user record (includes credentials and settings)
                conn.execute(
                    "DELETE FROM users WHERE chat_id = ?",
                    (chat_id,)
                )
                conn.commit()
                logger.info(f"Cleared all data for user {chat_id}")
                return True
        except Exception as e:
            logger.error(f"Error clearing user data for {chat_id}: {e}")
            return False
