from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class CalendarService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("calendar", "v3", credentials=credentials)
        self.account_name = account_name

    # ------------------------------------------------------------------ calendars

    def list_calendars(self) -> List[Dict[str, Any]]:
        result = self.service.calendarList().list().execute()
        return [
            {
                "id": cal["id"],
                "summary": cal.get("summary", ""),
                "description": cal.get("description", ""),
                "primary": cal.get("primary", False),
                "accessRole": cal.get("accessRole", ""),
                "backgroundColor": cal.get("backgroundColor", ""),
            }
            for cal in result.get("items", [])
        ]

    # ------------------------------------------------------------------ events - READ

    def list_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "maxResults": min(max_results, 50),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min or now,
        }
        if time_max:
            params["timeMax"] = time_max

        result = self.service.events().list(**params).execute()
        events = [self._parse_event(e) for e in result.get("items", [])]
        return {
            "calendar_id": calendar_id,
            "count": len(events),
            "events": events,
            "nextPageToken": result.get("nextPageToken"),
        }

    def search_events(
        self,
        query: str,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "q": query,
            "maxResults": min(max_results, 50),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min or now,
        }
        if time_max:
            params["timeMax"] = time_max

        result = self.service.events().list(**params).execute()
        events = [self._parse_event(e) for e in result.get("items", [])]
        return {
            "query": query,
            "count": len(events),
            "events": events,
        }

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict[str, Any]:
        event = self.service.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()
        return self._parse_event(event)

    # ------------------------------------------------------------------ events - CREATE

    def create_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        calendar_id: str = "primary",
        description: str = "",
        location: str = "",
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new calendar event."""
        event = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }

        if attendees:
            event["attendees"] = [{"email": email} for email in attendees]

        created_event = self.service.events().insert(
            calendarId=calendar_id, body=event
        ).execute()
        return self._parse_event(created_event)

    # ------------------------------------------------------------------ events - UPDATE

    def update_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        title: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an existing calendar event."""
        event = self.service.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()

        if title is not None:
            event["summary"] = title
        if description is not None:
            event["description"] = description
        if location is not None:
            event["location"] = location
        if start_time is not None:
            event["start"] = {"dateTime": start_time}
        if end_time is not None:
            event["end"] = {"dateTime": end_time}
        if attendees is not None:
            event["attendees"] = [{"email": email} for email in attendees]

        updated_event = self.service.events().update(
            calendarId=calendar_id, eventId=event_id, body=event
        ).execute()
        return self._parse_event(updated_event)

    # ------------------------------------------------------------------ events - DELETE

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> Dict[str, str]:
        """Delete a calendar event."""
        self.service.events().delete(
            calendarId=calendar_id, eventId=event_id
        ).execute()
        return {
            "status": "deleted",
            "event_id": event_id,
            "calendar_id": calendar_id,
            "message": f"Event {event_id} deleted from {calendar_id}",
        }

    # ------------------------------------------------------------------ sync / conflict blocking

    def sync_blocks_from(
        self,
        source_service: "CalendarService",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        source_calendar_id: str = "primary",
        target_calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        """
        Fetch events from source_service and create block events on this calendar
        so that every occupied slot is marked busy with a label like
        '[personal] Booked at 10:00 AM'.
        Returns a summary of how many blocks were created or skipped.
        """
        source_events = source_service.list_events(
            time_min=time_min,
            time_max=time_max,
            max_results=50,
            calendar_id=source_calendar_id,
        )["events"]

        created = []
        skipped = []

        for event in source_events:
            # Skip all-day events — they don't have a specific time slot
            if event["allDay"]:
                skipped.append({"reason": "all-day", "summary": event["summary"]})
                continue

            start_dt = event["start"]
            end_dt = event["end"]

            # Build a human-readable time label from the ISO start string
            try:
                dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                time_label = dt.strftime("%-I:%M %p")
            except Exception:
                time_label = start_dt

            block_title = f"[{source_service.account_name}] Booked at {time_label}"
            block_description = (
                f"This slot is blocked because {source_service.account_name} has: "
                f'"{event["summary"]}" at this time.'
            )

            # Check if an identical block already exists to avoid duplicates
            existing = self.service.events().list(
                calendarId=target_calendar_id,
                timeMin=start_dt if start_dt.endswith("Z") or "+" in start_dt else start_dt + "Z",
                timeMax=end_dt if end_dt.endswith("Z") or "+" in end_dt else end_dt + "Z",
                q=block_title,
                singleEvents=True,
            ).execute().get("items", [])

            if existing:
                skipped.append({"reason": "already exists", "summary": block_title})
                continue

            created_event = self.create_event(
                title=block_title,
                start_time=start_dt,
                end_time=end_dt,
                calendar_id=target_calendar_id,
                description=block_description,
            )
            created.append(created_event)

        return {
            "source_account": source_service.account_name,
            "target_account": self.account_name,
            "blocks_created": len(created),
            "blocks_skipped": len(skipped),
            "created": created,
            "skipped": skipped,
        }

    # ------------------------------------------------------------------ internals

    def _parse_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        start = event.get("start", {})
        end = event.get("end", {})

        attendees = [
            {
                "email": a.get("email", ""),
                "name": a.get("displayName", ""),
                "response": a.get("responseStatus", ""),
                "self": a.get("self", False),
            }
            for a in event.get("attendees", [])
        ]

        return {
            "id": event.get("id", ""),
            "summary": event.get("summary", "(sin título)"),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "allDay": "date" in start and "dateTime" not in start,
            "status": event.get("status", ""),
            "organizer": event.get("organizer", {}).get("email", ""),
            "attendees": attendees,
            "attendeeCount": len(attendees),
            "meetLink": event.get("hangoutLink", ""),
            "htmlLink": event.get("htmlLink", ""),
            "recurrence": bool(event.get("recurrence") or event.get("recurringEventId")),
        }
