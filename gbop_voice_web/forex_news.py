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
FF_CACHE_URL = os.getenv(
    "GBOP_FF_CACHE_URL",
    "https://raw.githubusercontent.com/Rexercise/GBOP/calendar-cache/ff_calendar_thisweek.json",
)
FF_NEWS_LEAD_MINUTES = max(
    1,
    int(os.getenv("GBOP_FF_NEWS_LEAD_MINUTES", "5")),
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
    """Prefer Forex Factory directly, then fall back to GBOP's GitHub mirror."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.forexfactory.com/",
    }
    failures = []

    with httpx.Client(
        timeout=FF_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=headers,
    ) as client:
        for source, url in (
            ("forex_factory", FF_CALENDAR_URL),
            ("github_cache", FF_CACHE_URL),
        ):
            try:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (list, dict)):
                    raise ValueError("calendar payload is not JSON rows")
                if source == "github_cache":
                    logger.info(
                        "Forex Factory direct feed unavailable; using GBOP GitHub calendar cache."
                    )
                return payload
            except Exception as exc:
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )
                failures.append(
                    f"{source}:{type(exc).__name__}"
                    + (f":{status}" if status is not None else "")
                )
                if source == "forex_factory":
                    logger.info(
                        "Forex Factory direct feed unavailable (%s); trying GitHub cache.",
                        failures[-1],
                    )

    raise RuntimeError("calendar sources unavailable: " + ", ".join(failures))


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
        logger.warning(
            "Forex Factory calendar fetch failed; using cached events: %s",
            str(exc)[:300],
        )
        return list(_CACHE_EVENTS)

    _CACHE_EVENTS = events
    _CACHE_LAST_SUCCESS_MONO = time.monotonic()
    return list(_CACHE_EVENTS)


def format_pre_shift_news(
    events: Iterable[ForexNewsEvent],
    now: datetime,
    *,
    shift: str,
) -> str:
    """Summarize the current Eastern calendar day's red-folder schedule."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN_TZ)
    now_et = now.astimezone(EASTERN_TZ)
    today = sorted(
        (
            event
            for event in events
            if event.scheduled_at.astimezone(EASTERN_TZ).date() == now_et.date()
        ),
        key=lambda event: (
            event.scheduled_at,
            event.currency,
            event.title.casefold(),
        ),
    )
    if not today:
        return ""

    upcoming = [
        event
        for event in today
        if event.scheduled_at.astimezone(EASTERN_TZ) > now_et
    ]
    label = "Day Shift" if (shift or "").lower() == "day" else "Night Shift"
    shift_start = 9 if (shift or "").lower() == "day" else 21
    shift_end = 12 if (shift or "").lower() == "day" else 24

    lines = ["🔴 **Forex Factory red-folder news today:**"]
    if upcoming:
        for event in upcoming[:6]:
            event_et = event.scheduled_at.astimezone(EASTERN_TZ)
            in_shift = shift_start <= event_et.hour < shift_end
            tag = f" — during {label}" if in_shift else ""
            lines.append(
                f"• **{event_et.strftime('%-I:%M %p')} ET — "
                f"{event.currency} — {event.title}**{tag}"
            )
        if len(upcoming) > 6:
            lines.append(f"• +{len(upcoming) - 6} more high-impact event(s) today")
    else:
        recent = today[-3:]
        lines.append("No additional red-folder releases remain today. Earlier:")
        for event in recent:
            event_et = event.scheduled_at.astimezone(EASTERN_TZ)
            lines.append(
                f"• {event_et.strftime('%-I:%M %p')} ET — "
                f"{event.currency} — {event.title}"
            )

    lines.append(
        "Keep any upcoming binary event inside your personal trading and risk plan."
    )
    return "\n".join(lines)


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
    trading_plan: str = "",
    risk_plan: str = "",
    personal_rule: str = "",
) -> str:
    events = list(events)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN_TZ)
    now_et = now.astimezone(EASTERN_TZ)

    lines = [
        "🔴 **Forex Factory Red Folder — 5 Minute Reminder**"
        if int(lead_minutes) == 5
        else "🔴 **Forex Factory Red Folder Alert**",
        "",
        (
            "**Binary event coming up.** "
            f"High-impact news is scheduled within the next {int(lead_minutes)} minutes:"
        ),
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

    if trading_plan:
        lines.extend(("", "**Your trading plan:** " + _text(trading_plan)[:500]))
    if risk_plan:
        lines.append("**Your risk plan:** " + _text(risk_plan)[:420])

    if personal_rule:
        lines.extend(("", "**Your personal Never Again rule:** " + _text(personal_rule)[:380]))

    lines.extend(
        (
            "",
            "⚠️ **News-risk reminder:** Treat the release as a binary event and stay "
            "inside your own predefined setup criteria and saved risk plan.",
            "<https://www.forexfactory.com/calendar>",
        )
    )
    return "\n".join(lines)[:1900]
