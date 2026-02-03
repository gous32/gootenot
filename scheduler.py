"""Scheduling and polling logic for calendar notifications."""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot
from telegram.error import TelegramError

import config
from database import Database
from calendar_service import CalendarService, format_event_message

logger = logging.getLogger(__name__)


class NotificationScheduler:
    """Manages polling and scheduled notifications."""

    def __init__(self, bot: Bot, database: Database):
        self.bot = bot
        self.db = database
        self.scheduler = AsyncIOScheduler()

    def start(self):
        """Start all scheduled jobs."""
        # Polling job - check for new/updated events
        self.scheduler.add_job(
            self.poll_all_users,
            trigger=IntervalTrigger(seconds=config.POLL_INTERVAL_SECONDS),
            id='poll_calendar',
            name='Poll calendar for changes',
            replace_existing=True
        )

        # Daily summary job
        hour, minute = config.DAILY_SUMMARY_TIME.split(':')
        self.scheduler.add_job(
            self.send_daily_summaries,
            trigger=CronTrigger(hour=int(hour), minute=int(minute)),
            id='daily_summary',
            name='Send daily summaries',
            replace_existing=True
        )

        # Cleanup old notification records weekly
        self.scheduler.add_job(
            self.db.cleanup_old_notifications,
            trigger=CronTrigger(day_of_week='sun', hour=3),
            id='cleanup',
            name='Cleanup old notifications',
            replace_existing=True
        )

        self.scheduler.start()
        logger.info("Scheduler started")

    def stop(self):
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("Scheduler stopped")

    async def poll_all_users(self):
        """Poll calendar for all registered users."""
        logger.debug("Starting calendar poll for all users")

        for chat_id in self.db.get_all_users():
            try:
                await self.poll_user_calendar(chat_id)
            except Exception as e:
                logger.error(f"Error polling calendar for user {chat_id}: {e}")

    async def poll_user_calendar(self, chat_id: int):
        """Poll calendar for a specific user and send notifications."""
        # Get user's credentials
        creds_json = self.db.get_user_credentials(chat_id)
        if not creds_json:
            logger.debug(f"No credentials for user {chat_id}, skipping")
            return

        try:
            calendar = CalendarService.from_credentials_json(creds_json)
        except Exception as e:
            logger.error(f"Failed to create calendar service for user {chat_id}: {e}")
            return

        # Get last poll time
        last_poll = self.db.get_last_poll_time(chat_id)
        current_time = datetime.utcnow()

        # First poll: get upcoming events and silently record them (no notifications)
        is_first_poll = last_poll is None
        if is_first_poll:
            events = calendar.get_upcoming_events()
            logger.info(f"First poll for user {chat_id}: {len(events)} upcoming events (recording without notifications)")
        else:
            # Get events changed since last poll
            events = calendar.get_changed_events(updated_min=last_poll)
            logger.debug(f"Poll for user {chat_id}: {len(events)} changed events")

        # Process each event
        for event in events:
            await self.process_event(chat_id, event, calendar, silent=is_first_poll)

        # Update last poll time
        self.db.update_last_poll_time(chat_id, current_time)

        # Update credentials if refreshed
        new_creds = calendar.get_credentials_json()
        if new_creds != creds_json:
            self.db.save_user_credentials(chat_id, new_creds)

    async def process_event(self, chat_id: int, event: Dict, calendar: CalendarService, silent: bool = False):
        """Process a single event and send appropriate notifications.

        Args:
            chat_id: User's Telegram chat ID
            event: Calendar event dictionary
            calendar: CalendarService instance
            silent: If True, only mark events as notified without sending messages (for first poll)
        """
        event_id = event['id']
        event_updated = event.get('updated', '')
        calendar_id = 'primary'

        # Skip cancelled events
        if event.get('status') == 'cancelled':
            return

        # Check if event was created or modified
        if not self.db.has_notification_sent(chat_id, event_id, 'created'):
            # New event
            if not silent:
                await self.send_notification(
                    chat_id,
                    f"🆕 *New Event*\n\n{format_event_message(event)}"
                )
            self.db.mark_notification_sent(
                chat_id, event_id, calendar_id, event_updated, 'created'
            )
        elif not self.db.has_notification_sent(chat_id, event_id, f'modified_{event_updated}'):
            # Modified event
            if not silent:
                await self.send_notification(
                    chat_id,
                    f"✏️ *Event Updated*\n\n{format_event_message(event)}"
                )
            self.db.mark_notification_sent(
                chat_id, event_id, calendar_id, event_updated, f'modified_{event_updated}'
            )

        # Check for upcoming reminder notifications (skip on first poll)
        if not silent:
            await self.check_reminders(chat_id, event, calendar_id, event_updated)

    async def check_reminders(
        self,
        chat_id: int,
        event: Dict,
        calendar_id: str,
        event_updated: str
    ):
        """Check if reminder notifications should be sent for an event."""
        start = event.get('start', {})

        # Skip all-day events for reminders
        if 'date' in start:
            return

        try:
            start_dt = datetime.fromisoformat(start.get('dateTime', '').replace('Z', '+00:00'))
        except ValueError:
            logger.warning(f"Invalid datetime format for event {event['id']}")
            return

        now = datetime.utcnow().replace(tzinfo=start_dt.tzinfo)
        time_until = start_dt - now

        # Get user's reminder preferences
        reminder_times = self.db.get_reminder_times(chat_id)

        for minutes in reminder_times:
            reminder_window = timedelta(minutes=minutes)
            notification_type = f'reminder_{minutes}'

            # Check if we're within the reminder window
            if timedelta(0) <= time_until <= reminder_window + timedelta(minutes=config.POLL_INTERVAL_SECONDS / 60):
                if not self.db.has_notification_sent(chat_id, event['id'], notification_type):
                    await self.send_notification(
                        chat_id,
                        f"⏰ *Reminder: Event in {minutes} minutes*\n\n{format_event_message(event)}"
                    )
                    self.db.mark_notification_sent(
                        chat_id, event['id'], calendar_id, event_updated, notification_type
                    )

    async def send_daily_summaries(self):
        """Send daily summary to all users with summaries enabled."""
        logger.info("Sending daily summaries")

        for chat_id in self.db.get_all_users():
            try:
                await self.send_daily_summary(chat_id)
            except Exception as e:
                logger.error(f"Error sending daily summary to user {chat_id}: {e}")

    async def send_daily_summary(self, chat_id: int):
        """Send daily event summary to a user."""
        creds_json = self.db.get_user_credentials(chat_id)
        if not creds_json:
            return

        try:
            calendar = CalendarService.from_credentials_json(creds_json)
            today = datetime.utcnow()
            events = calendar.get_events_for_day(today)

            if not events:
                message = "📅 *Today's Schedule*\n\nNo events scheduled for today."
            else:
                message = f"📅 *Today's Schedule* ({len(events)} events)\n\n"
                for event in events:
                    message += format_event_message(event) + "\n---\n"

            await self.send_notification(chat_id, message)

        except Exception as e:
            logger.error(f"Error generating daily summary for user {chat_id}: {e}")

    async def send_notification(self, chat_id: int, message: str):
        """Send a notification message to a user."""
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='Markdown'
            )
            logger.debug(f"Sent notification to {chat_id}")
        except TelegramError as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
