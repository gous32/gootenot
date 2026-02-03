"""Scheduling and polling logic for calendar notifications."""
import logging
from datetime import datetime, timedelta, timezone
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

        # Get upcoming events
        events = calendar.get_upcoming_events()
        logger.info(f"Poll for user {chat_id}: {len(events)} upcoming events")

        # Process each event
        for event in events:
            event_summary = event.get('summary', 'No title')
            logger.debug(f"Processing event: {event_summary}")
            await self.process_event(chat_id, event, calendar)

        # Update last poll time
        self.db.update_last_poll_time(chat_id, current_time)

        # Update credentials if refreshed
        new_creds = calendar.get_credentials_json()
        if new_creds != creds_json:
            self.db.save_user_credentials(chat_id, new_creds)

    async def process_event(self, chat_id: int, event: Dict, calendar: CalendarService):
        """Process a single event and send appropriate notifications.

        Args:
            chat_id: User's Telegram chat ID
            event: Calendar event dictionary
            calendar: CalendarService instance
        """
        event_id = event['id']
        event_updated = event.get('updated', '')
        calendar_id = 'primary'

        # Skip cancelled events
        if event.get('status') == 'cancelled':
            return

        await self.check_reminders(chat_id, event, calendar_id, event_updated)

        # Check if event was created or modified
        if not self.db.has_notification_sent(chat_id, event_id, 'created'):
            # New event
            await self.send_notification(
                chat_id,
                f"🆕 *New Event*\n\n{format_event_message(event)}"
            )
            self.db.mark_notification_sent(
                chat_id, event_id, calendar_id, event_updated, 'created'
            )
        elif not self.db.has_notification_sent(chat_id, event_id, f'modified_{event_updated}'):
            # Modified event - clear old reminder marks so new reminders can be sent
            self.db.clear_event_reminder_notifications(chat_id, event_id)

            await self.send_notification(
                chat_id,
                f"✏️ *Event Updated*\n\n{format_event_message(event)}"
            )
            self.db.mark_notification_sent(
                chat_id, event_id, calendar_id, event_updated, f'modified_{event_updated}'
            )

    async def check_reminders(
        self,
        chat_id: int,
        event: Dict,
        calendar_id: str,
        event_updated: str
    ):
        """Check if reminder notifications should be sent for an event."""
        start = event.get('start', {})
        event_id = event.get('id')
        event_summary = event.get('summary', 'No title')

        # Skip all-day events for reminders
        if 'date' in start:
            logger.debug(f"Skipping all-day event: {event_summary}")
            return

        try:
            start_datetime_str = start.get('dateTime', '')
            start_dt = datetime.fromisoformat(start_datetime_str.replace('Z', '+00:00'))
        except ValueError:
            logger.warning(f"Invalid datetime format for event {event_id}")
            return

        # Get current time in UTC (timezone-aware) for proper comparison
        now = datetime.now(timezone.utc)
        time_until = start_dt - now
        time_until_minutes = time_until.total_seconds() / 60

        logger.info(
            f"Checking reminders for '{event_summary}' | "
            f"Event time: {start_datetime_str} | "
            f"Now: {now.isoformat()} | "
            f"Starts in: {time_until_minutes:.1f} min"
        )

        # Get user's reminder preferences
        reminder_times = self.db.get_reminder_times(chat_id)
        logger.debug(f"User reminder times: {reminder_times} minutes")

        for minutes in reminder_times:
            reminder_window = timedelta(minutes=minutes)
            notification_type = f'reminder_{minutes}'
            poll_buffer = timedelta(minutes=config.POLL_INTERVAL_SECONDS / 60)

            lower_bound = (reminder_window - poll_buffer).total_seconds() / 60
            upper_bound = (reminder_window + poll_buffer).total_seconds() / 60

            in_window = reminder_window - poll_buffer <= time_until <= reminder_window + poll_buffer
            already_sent = self.db.has_notification_sent(chat_id, event_id, notification_type)

            logger.debug(
                f"  Reminder {minutes}min: time_until={time_until_minutes:.1f}min, "
                f"window=[{lower_bound:.1f}, {upper_bound:.1f}], "
                f"in_window={in_window}, already_sent={already_sent}"
            )

            # Check if we're near the reminder time (within one poll interval)
            if in_window:
                if not already_sent:
                    logger.info(f"🔔 Sending {minutes}min reminder for '{event_summary}'")
                    await self.send_notification(
                        chat_id,
                        f"⏰ *Reminder: Event in {minutes} minutes*\n\n{format_event_message(event)}"
                    )
                    self.db.mark_notification_sent(
                        chat_id, event_id, calendar_id, event_updated, notification_type
                    )
                else:
                    logger.debug(f"  Reminder {minutes}min already sent, skipping")

        logger.info(
            f"Checking reminders for '{event_summary}' done"
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
