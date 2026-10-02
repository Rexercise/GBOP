from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _period_bounds_utc(start_day: date, end_day_exclusive: date, tz: ZoneInfo):
    start_local = datetime.combine(start_day, time.min, tzinfo=tz)
    end_local = datetime.combine(end_day_exclusive, time.min, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).isoformat(),
        end_local.astimezone(timezone.utc).isoformat(),
    )


def daily_period(target_day: date, tz: ZoneInfo):
    start_utc, end_utc = _period_bounds_utc(
        target_day,
        target_day + timedelta(days=1),
        tz,
    )
    return {
        "kind": "daily",
        "start_day": target_day,
        "end_day": target_day,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "label": target_day.strftime("%a, %b %-d, %Y"),
    }


def weekly_period(week_end_day: date, tz: ZoneInfo):
    start_day = week_end_day - timedelta(days=4)
    start_utc, end_utc = _period_bounds_utc(
        start_day,
        week_end_day + timedelta(days=1),
        tz,
    )
    return {
        "kind": "weekly",
        "start_day": start_day,
        "end_day": week_end_day,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "label": (
            f"{start_day.strftime('%b %-d')}–"
            f"{week_end_day.strftime('%b %-d, %Y')}"
        ),
    }


def _normalize_session(value):
    text = (value or "").strip().lower()
    if "night" in text:
        return "Night Shift"
    if "day" in text or "new york" in text:
        return "Day Shift"
    return (value or "Unspecified").strip() or "Unspecified"


def _adherence_bucket(value):
    text = (value or "").strip().lower()
    if not text:
        return "unknown"
    if "partial" in text or "deviat" in text:
        return "partial"
    if "violat" in text or "broke" in text or "broken" in text:
        return "violated"
    if (
        "follow" in text
        or "adher" in text
        or text in {"yes", "y", "clean"}
    ):
        return "followed"
    return "unknown"


def _trade_breakdown(rows, key):
    groups = defaultdict(
        lambda: {
            "count": 0,
            "scored": 0,
            "net_r": 0.0,
            "wins": 0,
        }
    )
    for row in rows:
        name = (row.get(key) or "Unspecified").strip() or "Unspecified"
        if key == "session":
            name = _normalize_session(name)
        group = groups[name]
        group["count"] += 1
        result = row.get("final_result_r")
        if result is not None:
            value = float(result)
            group["scored"] += 1
            group["net_r"] += value
            if value > 0:
                group["wins"] += 1

    output = []
    for name, item in groups.items():
        scored = item["scored"]
        output.append({
            "name": name,
            "count": item["count"],
            "net_r": item["net_r"],
            "win_rate": (
                item["wins"] / scored * 100.0
                if scored else None
            ),
        })

    return sorted(
        output,
        key=lambda item: (item["net_r"], item["count"]),
        reverse=True,
    )


def _execution_breakdown(rows, key):
    groups = defaultdict(lambda: {"count": 0, "risk_r": 0.0})
    for row in rows:
        raw = row.get(key)
        name = str(raw if raw is not None else "Unspecified")
        groups[name]["count"] += 1
        groups[name]["risk_r"] += float(row.get("risk_r") or 0.0)

    return [
        {
            "name": name,
            "count": item["count"],
            "risk_r": item["risk_r"],
        }
        for name, item in sorted(
            groups.items(),
            key=lambda pair: (
                pair[1]["count"],
                pair[1]["risk_r"],
            ),
            reverse=True,
        )
    ]


def _streaks(scored_rows):
    max_wins = max_losses = wins = losses = 0

    for row in scored_rows:
        value = float(row["final_result_r"])
        if value > 0:
            wins += 1
            losses = 0
        elif value < 0:
            losses += 1
            wins = 0
        else:
            wins = losses = 0

        max_wins = max(max_wins, wins)
        max_losses = max(max_losses, losses)

    return max_wins, max_losses


def _top_or_none(items):
    return items[0] if items else None


def _bottom_or_none(items):
    return (
        min(
            items,
            key=lambda item: (
                item["net_r"],
                -item["count"],
            ),
        )
        if items else None
    )


def _recommendation(stats):
    performance = stats["performance"]
    process = stats["process"]
    weakest = stats.get("weakest_session")

    if performance["scored_trades"] == 0:
        return (
            "No closed, scored trades in this period. Keep risk fixed, "
            "finish the journal/check-ins, and wait for a real sample "
            "before changing the plan."
        )

    if process["risk_flags"] > 0:
        return (
            "Priority next shift: remove the risk-protocol violations. "
            "Keep every entry inside the planned tier allocation and stop "
            "adding once the thesis risk budget is used."
        )

    if (
        process["violated"] > 0
        or process["partial"] > process["followed"]
    ):
        return (
            "Priority next shift: tighten execution to your written plan. "
            "Only take setups that meet your A+ criteria and remove "
            "discretionary entries that create partial or broken adherence."
        )

    if weakest and weakest["net_r"] < 0:
        return (
            f"Protect what is working, but tighten criteria in "
            f"{weakest['name']}; it was the weakest session in this sample "
            f"at {weakest['net_r']:+.2f}R."
        )

    if (
        performance["net_r"] < 0
        and performance["closed_trades"] >= 3
    ):
        return (
            "The sample finished negative without a process-violation "
            "signal. Keep size unchanged and reduce decision frequency "
            "next shift; prioritize the clearest A+ setup instead of "
            "trying to win the day back."
        )

    return (
        "Process is carrying the results. Keep size unchanged, repeat the "
        "strongest setups, and protect the same rule adherence rather than "
        "increasing risk after a good period."
    )


def collect_snapshot(
    db_factory,
    guild_id: int,
    user_id: int,
    period: dict,
):
    start_utc = period["start_utc"]
    end_utc = period["end_utc"]
    start_date = period["start_day"].isoformat()
    end_date = period["end_day"].isoformat()

    with db_factory() as conn:
        closed = conn.execute(
            """
            SELECT
                id,
                asset,
                direction,
                play,
                session,
                final_result_r,
                created_at,
                closed_at
            FROM theses
            WHERE guild_id=?
              AND user_id=?
              AND status='CLOSED'
              AND closed_at IS NOT NULL
              AND closed_at>=?
              AND closed_at<?
            ORDER BY closed_at, id
            """,
            (
                guild_id,
                user_id,
                start_utc,
                end_utc,
            ),
        ).fetchall()

        opened_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM theses
            WHERE guild_id=?
              AND user_id=?
              AND created_at>=?
              AND created_at<?
            """,
            (
                guild_id,
                user_id,
                start_utc,
                end_utc,
            ),
        ).fetchone()[0]

        open_now = conn.execute(
            """
            SELECT COUNT(*)
            FROM theses
            WHERE guild_id=?
              AND user_id=?
              AND status='OPEN'
            """,
            (
                guild_id,
                user_id,
            ),
        ).fetchone()[0]

        executions = conn.execute(
            """
            SELECT
                thesis_id,
                entry_model,
                tier,
                risk_r,
                created_at
            FROM thesis_executions
            WHERE guild_id=?
              AND user_id=?
              AND created_at>=?
              AND created_at<?
            ORDER BY created_at, id
            """,
            (
                guild_id,
                user_id,
                start_utc,
                end_utc,
            ),
        ).fetchall()

        flags = conn.execute(
            """
            SELECT
                rule_code,
                message,
                created_at
            FROM risk_flags
            WHERE guild_id=?
              AND user_id=?
              AND created_at>=?
              AND created_at<?
            ORDER BY created_at, id
            """,
            (
                guild_id,
                user_id,
                start_utc,
                end_utc,
            ),
        ).fetchall()

        journals = conn.execute(
            """
            SELECT
                rule_adherence,
                result_r,
                study_note,
                created_at
            FROM journals
            WHERE guild_id=?
              AND user_id=?
              AND created_at>=?
              AND created_at<?
            ORDER BY created_at, id
            """,
            (
                guild_id,
                user_id,
                start_utc,
                end_utc,
            ),
        ).fetchall()

        checkins = conn.execute(
            """
            SELECT
                shift,
                response,
                responded_at,
                shift_date
            FROM post_shift_checkins
            WHERE guild_id=?
              AND user_id=?
              AND shift_date>=?
              AND shift_date<=?
            ORDER BY shift_date, id
            """,
            (
                guild_id,
                user_id,
                start_date,
                end_date,
            ),
        ).fetchall()

    scored = [
        row
        for row in closed
        if row.get("final_result_r") is not None
    ]
    results = [
        float(row["final_result_r"])
        for row in scored
    ]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value < 0]
    breakeven = [value for value in results if value == 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_r = sum(results)
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss
        else (float("inf") if gross_profit else None)
    )
    max_win_streak, max_loss_streak = _streaks(scored)

    adherence = Counter(
        _adherence_bucket(row.get("rule_adherence"))
        for row in journals
    )
    known_adherence = (
        adherence["followed"]
        + adherence["partial"]
        + adherence["violated"]
    )
    full_adherence_rate = (
        adherence["followed"] / known_adherence * 100.0
        if known_adherence
        else None
    )

    play_breakdown = _trade_breakdown(closed, "play")
    asset_breakdown = _trade_breakdown(closed, "asset")
    session_breakdown = _trade_breakdown(closed, "session")
    model_breakdown = _execution_breakdown(
        executions,
        "entry_model",
    )
    tier_breakdown = _execution_breakdown(
        executions,
        "tier",
    )

    total_execution_risk = sum(
        float(row.get("risk_r") or 0.0)
        for row in executions
    )

    stats = {
        "period": period,
        "performance": {
            "opened_trades": int(opened_count or 0),
            "closed_trades": len(closed),
            "scored_trades": len(scored),
            "unscored_trades": len(closed) - len(scored),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": (
                len(wins) / len(scored) * 100.0
                if scored else None
            ),
            "net_r": net_r,
            "avg_r": (
                net_r / len(scored)
                if scored else None
            ),
            "avg_win_r": (
                sum(wins) / len(wins)
                if wins else None
            ),
            "avg_loss_r": (
                sum(losses) / len(losses)
                if losses else None
            ),
            "profit_factor": profit_factor,
            "best_r": max(results) if results else None,
            "worst_r": min(results) if results else None,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "open_now": int(open_now or 0),
        },
        "execution": {
            "count": len(executions),
            "risk_r": total_execution_risk,
            "avg_risk_r": (
                total_execution_risk / len(executions)
                if executions else None
            ),
            "by_model": model_breakdown,
            "by_tier": tier_breakdown,
        },
        "process": {
            "journals": len(journals),
            "followed": adherence["followed"],
            "partial": adherence["partial"],
            "violated": adherence["violated"],
            "unknown": adherence["unknown"],
            "full_adherence_rate": full_adherence_rate,
            "risk_flags": len(flags),
            "checkins_expected": len(checkins),
            "checkins_completed": sum(
                1
                for row in checkins
                if (row.get("response") or "").strip()
            ),
        },
        "by_play": play_breakdown,
        "by_asset": asset_breakdown,
        "by_session": session_breakdown,
        "top_play": _top_or_none(play_breakdown),
        "top_asset": _top_or_none(asset_breakdown),
        "strongest_session": _top_or_none(session_breakdown),
        "weakest_session": _bottom_or_none(session_breakdown),
    }
    stats["recommendation"] = _recommendation(stats)
    return stats


def format_profit_factor(value):
    if value is None:
        return "—"
    if value == float("inf"):
        return "∞"
    return f"{value:.2f}"


def format_percent(value):
    return "—" if value is None else f"{value:.0f}%"


def format_r(value):
    return "—" if value is None else f"{float(value):+.2f}R"


def format_trade_breakdown(items, limit=4):
    if not items:
        return "No scored trades"

    lines = []
    for item in items[:limit]:
        win_rate = (
            ""
            if item["win_rate"] is None
            else f" | {item['win_rate']:.0f}% WR"
        )
        lines.append(
            f"• {item['name']}: {item['net_r']:+.2f}R | "
            f"{item['count']} trade(s){win_rate}"
        )

    return "\n".join(lines)


def format_execution_breakdown(items, limit=4):
    if not items:
        return "No executions"

    return "\n".join(
        f"• {item['name']}: {item['count']} | "
        f"{item['risk_r']:.2f}R risk"
        for item in items[:limit]
    )
