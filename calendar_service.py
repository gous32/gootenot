"""Google Calendar API integration."""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger(__name__)


class CalendarService:
    """Wrapper for Google Calendar API operations."""

    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.service = build('calendar', 'v3', credentials=credentials)

    @classmethod
    def from_credentials_json(cls, credentials_json: str) -> 'CalendarService':
        """Create service from stored credentials JSON."""
        creds_data = json.loads(credentials_json)
        credentials = Credentials.from_authorized_user_info(creds_data, config.SCOPES)

        # Refresh if expired
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        return cls(credentials)

    @classmethod
    def get_authorization_url(cls) -> tuple[str, Any]:
        """Get OAuth authorization URL for user to visit."""
        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": config.GOOGLE_CLIENT_ID,
                    "client_secret": config.GOOGLE_CLIENT_SECRET,
                    "redirect_uris": [config.GOOGLE_REDIRECT_URI],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=config.SCOPES,
            redirect_uri=config.GOOGLE_REDIRECT_URI
        )

        auth_url, _ = flow.authorization_url(prompt='consent')
        return auth_url, flow

    @classmethod
    def exchange_code_for_credentials(cls, flow: Any, code: str) -> str:
        """Exchange authorization code for credentials and return JSON."""
        flow.fetch_token(code=code)
        credentials = flow.credentials
        return credentials.to_json()

    def get_credentials_json(self) -> str:
        """Get current credentials as JSON string."""
        return self.credentials.to_json()

    def get_upcoming_events(
        self,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get upcoming events from primary calendar.

        Args:
            time_min: Start time for events (default: now)
            time_max: End time for events (default: 7 days from now)
            max_results: Maximum number of events to return

        Returns:
            List of event dictionaries
        """
        if time_min is None:
            time_min = datetime.utcnow()
        if time_max is None:
            time_max = datetime.utcnow() + timedelta(days=7)

        try:
            events_result = self.service.events().list(
                calendarId='primary',
                timeMin=time_min.isoformat() + 'Z',
                timeMax=time_max.isoformat() + 'Z',
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            return events_result.get('items', [])

        except HttpError as error:
            logger.error(f"Error fetching events: {error}")
            return []

    def get_changed_events(
        self,
        updated_min: datetime,
        time_max: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Get events that were updated since a specific time.

        This is used for polling to detect new/modified events.

        Args:
            updated_min: Only return events updated after this time
            time_max: Optional max time for event start times

        Returns:
            List of event dictionaries
        """
        if time_max is None:
            time_max = datetime.utcnow() + timedelta(days=30)

        try:
            events_result = self.service.events().list(
                calendarId='primary',
                updatedMin=updated_min.isoformat() + 'Z',
                timeMax=time_max.isoformat() + 'Z',
                singleEvents=True,
                orderBy='updated'
            ).execute()

            return events_result.get('items', [])

        except HttpError as error:
            logger.error(f"Error fetching changed events: {error}")
            return []

    def get_events_for_day(self, target_date: datetime) -> List[Dict[str, Any]]:
        """Get all events for a specific day."""
        time_min = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=1)

        return self.get_upcoming_events(time_min=time_min, time_max=time_max)


def format_event_message(event: Dict[str, Any]) -> str:
    """Format an event as a human-readable message."""
    summary = event.get('summary', 'No title')
    start = event.get('start', {})
    end = event.get('end', {})

    # Handle all-day events
    if 'date' in start:
        start_str = start['date']
        time_info = "All day"
    else:
        start_dt = datetime.fromisoformat(start.get('dateTime', '').replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end.get('dateTime', '').replace('Z', '+00:00'))
        time_info = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
        start_str = start_dt.strftime('%Y-%m-%d')

    message = f"📅 *{summary}*\n"
    message += f"🕐 {time_info}\n"
    message += f"📆 {start_str}\n"

    if 'location' in event:
        message += f"📍 {event['location']}\n"

    if 'description' in event:
        desc = event['description'][:200]  # Truncate long descriptions
        message += f"\n{desc}\n"

    return message
