from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool


@tool
def schedule_meeting(
    attendees: list[str],
    subject: str,
    duration_minutes: int,
    preferred_day: datetime,
    start_time: int,
) -> str:
    """Schedule a calendar meeting."""
    date_str = preferred_day.strftime("%A, %B %d, %Y")
    return f"Meeting '{subject}' scheduled on {date_str} at {start_time} for {duration_minutes} minutes with {len(attendees)} attendees"


@tool
def check_calendar_availability(day: str) -> str:
    """Check calendar availability for a given day."""
    return f"Available times on {day}: 9:00 AM, 2:00 PM, 4:00 PM"


TOOLS = [schedule_meeting, check_calendar_availability]
TOOLS_PROMPT = """
1. schedule_meeting(attendees, subject, duration_minutes, preferred_day, start_time) - Schedule a meeting
2. check_calendar_availability(day) - Check available time slots for a given day
"""

# schedule_meeting commits a calendar action; reading availability is safe.
REQUIRES_APPROVAL = {"schedule_meeting"}
