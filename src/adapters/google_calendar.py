from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from src.utils.auth import get_google_credentials

class CalendarAdapter:
    def __init__(self, calendar_id: str):
        self.calendar_id = calendar_id
        try:
            self.creds = get_google_credentials()
            self.service = build('calendar', 'v3', credentials=self.creds)
        except Exception as e:
            print(f"Warning: Could not authorize Google Calendar ({e}). Entering Mock Mode.")
            self.service = None

    def create_event(self, title: str, description: str, date_obj=None):
        """
        Create a 1-hour event on the given date (default: now).
        """
        if not self.calendar_id:
            print("Calendar ID not set. Skipping event creation.")
            return

        now = datetime.now(timezone(timedelta(hours=9))) # KST
        if date_obj:
            # If date provided, set to 6 PM on that day
            start_time = datetime(date_obj.year, date_obj.month, date_obj.day, 18, 0, 0).isoformat() + '+09:00'
            end_time = datetime(date_obj.year, date_obj.month, date_obj.day, 19, 0, 0).isoformat() + '+09:00'
        else:
            start_time = now.isoformat()
            end_time = (now + timedelta(hours=1)).isoformat()

        event = {
            'summary': title,
            'description': description,
            'start': {
                'dateTime': start_time,
                'timeZone': 'Asia/Seoul',
            },
            'end': {
                'dateTime': end_time,
                'timeZone': 'Asia/Seoul',
            },
        }

        try:
            if self.service:
                self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
                print(f"Calendar event created: {title}")
            else:
                print(f"[Mock] Calendar event created: {title}")
        except Exception as e:
            print(f"Error creating calendar event: {e}")
