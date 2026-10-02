from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
import httpx
from zoneinfo import ZoneInfo


logger = logging.getLogger("gbop.forex_news")

FF_CALENDAR_URL = os.getenv(
    "GBOP_FF_CALENDAR_URL",
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
)
FF_NEWS_LEAD_MINUTES = max(
    1,
    int(os.getenv("GBOP_FF_NEWS_LEAD_MINUTES", "15")),
)
FF_REFRESH_SECONDS = max(
    300,
    int(os.getenv("GBOP_FF_REFRESH_SECONDS", "3600")),
)
FF_RETRY_SECONDS = max(
    60,
    int(os.getenv("GBOP_FF_RETRY_SECONDS", "300")),
)
FF_HTTP_TIMEOUT_SECONDS = max(
    3,
    int(os.getenv("GBOP_FF_HTTP_TIMEOUT_SECONDS", "10")),
)

EASTERN_TZ = ZoneInfo("America/New_York")

_CACHE_EVENTS: list["ForexNewsEvent"] = []
_CACHE_LAST_ATTEMPT_MONO: float | None = None
_CACHE_LAST_SUCCESS_MONO: float | None = None


@dataclass(frozen=True, slots=True)
class ForexNewsEvent:
    title: str
    currency: str
    scheduled_at: datetime
    impact: str = "High"
    forecast: str = ""
    previous: str = ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_date(value: Any, eastern_tz=EASTERN_TZ) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=eastern_tz)
        except (OverflowError, OSError, ValueError):
            return None

    raw = _text(value)
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=eastern_tz)
    return parsed.astimezone(eastern_tz)


def parse_high_impact_events(
    payload: Any,
    *,
    eastern_tz=EASTERN_TZ,
) -> list[ForexNewsEvent]:
    """Normalize Forex Factory's weekly JSON and keep only High-impact rows."""
    if isinstance(payload, dict):
        rows = payload.get("events", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    events: list[ForexNewsEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        impact = _text(row.get("impact"))
        if impact.casefold() != "high":
            continue

        scheduled_at = _parse_date(row.get("date"), eastern_tz=eastern_tz)
        if scheduled_at is None:
            continue

        title = _text(row.get("title"))
        currency = _text(row.get("country")).upper()
        if not title or not currency:
            continue

        events.append(
            ForexNewsEvent(
                title=title,
                currency=currency,
                scheduled_at=scheduled_at,
                impact="High",
                forecast=_text(row.get("forecast")),
                previous=_text(row.get("previous")),
            )
        )

    events.sort(
        key=lambda event: (
            event.scheduled_at,
            event.currency,
            event.title.casefold(),
        )
    )
    return events


def _download_calendar_payload() -> Any:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.forexfactory.com/",
    }
    with httpx.Client(
        timeout=FF_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = client.get(FF_CALENDAR_URL)
        response.raise_for_status()
        return response.json()


def get_high_impact_events(*, force: bool = False) -> list[ForexNewsEvent]:
    """Fetch with a one-hour cache and keep stale data if a refresh fails."""
    global _CACHE_EVENTS
    global _CACHE_LAST_ATTEMPT_MONO
    global _CACHE_LAST_SUCCESS_MONO

    now_mono = time.monotonic()

    if not force:
        if (
            _CACHE_LAST_SUCCESS_MONO is not None
            and now_mono - _CACHE_LAST_SUCCESS_MONO < FF_REFRESH_SECONDS
        ):
            return list(_CACHE_EVENTS)
        if (
            _CACHE_LAST_ATTEMPT_MONO is not None
            and now_mono - _CACHE_LAST_ATTEMPT_MONO < FF_RETRY_SECONDS
        ):
            return list(_CACHE_EVENTS)

    _CACHE_LAST_ATTEMPT_MONO = now_mono

    try:
        payload = _download_calendar_payload()
        events = parse_high_impact_events(payload)
    except Exception as exc:
        detail = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            detail += f" status={exc.response.status_code}"
        logger.warning(
            "Forex Factory calendar fetch failed; using cached events: %s",
            detail,
        )
        return list(_CACHE_EVENTS)

    _CACHE_EVENTS = events
    _CACHE_LAST_SUCCESS_MONO = time.monotonic()
    return list(_CACHE_EVENTS)


def due_high_impact_events(
    events: Iterable[ForexNewsEvent],
    now: datetime,
    *,
    lead_minutes: int = FF_NEWS_LEAD_MINUTES,
) -> list[ForexNewsEvent]:
    """Return unreleased events occurring within the configured lead window."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN_TZ)
    now_et = now.astimezone(EASTERN_TZ)

    lead_seconds = max(1, int(lead_minutes)) * 60
    due = []
    for event in events:
        seconds_until = (
            event.scheduled_at.astimezone(EASTERN_TZ) - now_et
        ).total_seconds()
        if 0 < seconds_until <= lead_seconds:
            due.append(event)

    return sorted(
        due,
        key=lambda event: (
            event.scheduled_at,
            event.currency,
            event.title.casefold(),
        ),
    )


def event_delivery_key(
    event: ForexNewsEvent,
    *,
    lead_minutes: int = FF_NEWS_LEAD_MINUTES,
) -> str:
    """Stable per-event key so each member receives one alert per release."""
    event_et = event.scheduled_at.astimezone(EASTERN_TZ)
    raw = "|".join(
        (
            event_et.isoformat(),
            event.currency.upper(),
            event.title.strip().casefold(),
        )
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:14]
    return (
        f"ff_red:{event_et:%Y%m%dT%H%M}:"
        f"{digest}:pre{int(lead_minutes)}"
    )


def format_red_folder_alert(
    events: Iterable[ForexNewsEvent],
    now: datetime,
    *,
    lead_minutes: int = FF_NEWS_LEAD_MINUTES,
) -> str:
    events = list(events)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN_TZ)
    now_et = now.astimezone(EASTERN_TZ)

    lines = [
        "🔴 **Forex Factory Red Folder Alert**",
        "",
        f"High-impact news is scheduled within the next {int(lead_minutes)} minutes:",
    ]

    for event in events[:8]:
        event_et = event.scheduled_at.astimezone(EASTERN_TZ)
        minutes = max(
            1,
            math.ceil((event_et - now_et).total_seconds() / 60),
        )
        line = (
            f"• **{event_et.strftime('%-I:%M %p')} ET — "
            f"{event.currency} — {event.title}** "
            f"(~{minutes} min)"
        )
        details = []
        if event.forecast:
            details.append(f"Forecast: {event.forecast}")
        if event.previous:
            details.append(f"Previous: {event.previous}")
        if details:
            line += "\n  " + " • ".join(details)
        lines.append(line)

    if len(events) > 8:
        lines.append(f"• +{len(events) - 8} more high-impact event(s)")

    lines.extend(
        (
            "",
            "⚠️ **GTOP risk reminder:** Never bring regular size into a binary event. Respect your personal risk protocol.",
            "<https://www.forexfactory.com/calendar>",
        )
    )
    return "\n".join(lines)[:1900]
