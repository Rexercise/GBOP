from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import re
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



def shift_period(target_day: date, shift: str, tz: ZoneInfo):
    """The established market shift, not the later formation send window."""
    if isinstance(target_day, str):
        target_day = date.fromisoformat(target_day)
    if shift not in {"day", "night"}:
        raise ValueError("shift must be day or night")
    start = datetime.combine(target_day, time(9 if shift == "day" else 21), tzinfo=tz)
    end = start + timedelta(hours=3)
    return {
        "kind": "shift", "shift": shift,
        "start_day": target_day, "end_day": target_day,
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end_utc": end.astimezone(timezone.utc).isoformat(),
        "label": f"{target_day:%a, %b %d, %Y} • {shift.title()} Shift",
    }


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _in_window(value, period):
    parsed = _timestamp(value)
    return bool(parsed and _timestamp(period["start_utc"]) <= parsed < _timestamp(period["end_utc"]))


def _metadata(value):
    try:
        result = json.loads(value or "{}") if isinstance(value, (str, bytes)) else value
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _table_exists(conn, table):
    # Fixed internal table names only; supported by both SQLite and db_compat.
    return bool(conn.execute(f"PRAGMA table_info({table})").fetchall())


def _selected_date(row, period):
    """Reported trade dates win. Logs never become inferred fills."""
    metadata = row["metadata"]
    for key in ("reported_exit_at",):
        if metadata.get(key):
            if _timestamp(metadata[key]) is None:
                return False, "invalid_date"
            return _in_window(metadata[key], period), "reported_time"
    if metadata.get("trade_date"):
        try:
            day = date.fromisoformat(metadata["trade_date"])
        except (ValueError, TypeError):
            return False, "invalid_date"
        if period.get("shift") and _normalize_session(row.get("session")) != period["shift"].title() + " Shift":
            return False, "unassigned_shift"
        return period["start_day"] <= day <= period["end_day"], "reported_date"
    if metadata.get("reported_entry_at"):
        if _timestamp(metadata["reported_entry_at"]) is None:
            return False, "invalid_date"
        return _in_window(metadata["reported_entry_at"], period), "reported_time"
    if row.get("status") == "CLOSED" and row.get("closed_at"):
        return _in_window(row["closed_at"], period), "close_log"
    return False, "undated"


def _review_records(conn, guild_id, user_id):
    """One outcome per canonical Trade #, plus explicitly real legacy journals.

    Reading must not invoke journal_rows/init_coach: scheduled reviews neither
    migrate schema nor canonicalize/alter historical records.
    """
    from gbop_voice_web.journal_numbers import journal_display
    theses = [dict(r) for r in conn.execute(
        "SELECT * FROM theses WHERE guild_id=? AND user_id=? ORDER BY id",
        (guild_id, user_id)).fetchall()]
    by_id = {r["id"]: r for r in theses}
    journals = [dict(r) for r in conn.execute(
        "SELECT * FROM journals WHERE guild_id=? AND user_id=? ORDER BY id",
        (guild_id, user_id)).fetchall()]
    details = {}
    if _table_exists(conn, "journal_details"):
        details = {r["journal_id"]: _metadata(r["metadata"]) for r in conn.execute(
            "SELECT journal_id,metadata FROM journal_details WHERE guild_id=? AND user_id=?",
            (guild_id, user_id)).fetchall()}
    display = {r["id"]: r for r in journal_display(conn, guild_id, user_id)}
    marked_theses = set()
    if _table_exists(conn, "thesis_events"):
        from gbop_voice_web.unified_journal import CANONICAL_EVENT
        marked_theses = {r["thesis_id"] for r in conn.execute(
            "SELECT thesis_id FROM thesis_events WHERE guild_id=? AND user_id=? AND event=?",
            (guild_id, user_id, CANONICAL_EVENT)).fetchall()}
    grouped = defaultdict(list)
    for row in journals:
        thesis_id = row.get("thesis_id")
        if thesis_id is not None and thesis_id not in by_id:
            continue  # inconsistent/foreign link is never followed or reassigned
        row["metadata"] = details.get(row["id"], {})
        row.update(display[row["id"]])
        grouped[("trade", thesis_id) if thesis_id else ("journal", row["id"])].append(row)
    for thesis in theses:
        grouped.setdefault(("trade", thesis["id"]), [])
    records = []
    for (kind, ident), history in grouped.items():
        thesis = by_id.get(ident, {}) if kind == "trade" else {}
        canonical = next((r for r in history if r.get("canonical")), None)
        chosen = dict(canonical or (history[0] if history else {}))
        if canonical is None and len(history) > 1:
            # Preserve only agreeing evidence; never select whichever legacy row
            # happens to sort last, including feelings/grades/date/adherence.
            chosen = {key: value for key, value in chosen.items()
                      if key != "metadata" and all(r.get(key) == value for r in history)}
            keys = set().union(*(r["metadata"] for r in history))
            chosen["metadata"] = {key: history[0]["metadata"].get(key) for key in keys
                if all(r["metadata"].get(key) == history[0]["metadata"].get(key) for r in history)}
            chosen["legacy_outcome_conflict"] = len({_finite(r.get("result_r")) for r in history}) > 1
        if kind == "trade" and ident in marked_theses and canonical is None:
            chosen = {"metadata": {}, "canonical_unavailable": True}
        metadata = chosen.get("metadata", {})
        record_kind = metadata.get("kind", "trade" if thesis else "reflection")
        if canonical is None and len({r["metadata"].get("kind", "trade" if thesis else "reflection") for r in history}) > 1:
            record_kind = "ambiguous"
        record = {**thesis, **chosen, "metadata": metadata,
                  "thesis_id": thesis.get("id"), "record_key": (kind, ident),
                  "has_journal": bool(history), "kind": record_kind,
                  "final_result_r": _finite(chosen.get("result_r") if history else thesis.get("final_result_r"))}
        # Do not overwrite thesis status/time with missing journal fields.
        for key in ("status", "closed_at"):
            record[key] = thesis.get(key)
        for key in ("asset", "play", "session"):
            record[key] = metadata.get(key) or thesis.get(key)
        records.append(record)
    return theses, records

def _normalize_session(value):
    text = str(value or "").strip().lower().replace("-", " ")
    if text in {"night", "night shift"}:
        return "Night Shift"
    if text in {"day", "day shift", "new york", "new york session"}:
        return "Day Shift"
    return str(value or "Unspecified").strip() or "Unspecified"


def _adherence_bucket(value):
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not text or text in {"unknown", "unspecified", "n/a"}:
        return "unknown"
    if re.search(r"(?:no|not|never|didn't|did not) (?:violation|violate|break|broke)", text):
        return "unknown"  # negated prose is not a positive adherence assessment
    if text in {"no", "n", "off-plan", "off plan"} or re.search(
            r"did(?:n['’]t| not) follow|not followed|did(?:n['’]t| not) adhere|violat|broke|broken", text):
        return "violated"
    if "partial" in text or "deviat" in text:
        return "partial"
    if text in {"yes", "y", "clean", "followed", "adhered", "followed plan", "followed the plan"}:
        return "followed"
    return "unknown"


def _record_adherence(row):
    metadata = row["metadata"]
    explicit = metadata.get("adherence") or row.get("rule_adherence")
    if explicit:
        return _adherence_bucket(explicit)
    try:
        from gbop_voice_web.trade_self_grades import self_grade_summary
        assessment = self_grade_summary(metadata, row.get("final_result_r"))
    except ImportError:
        assessment = None
    if assessment and not assessment.get("needs_clarification"):
        return {"followed_plan": "followed", "off_plan": "violated"}.get(assessment["adherence"], "unknown")
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
            "net_r": item["net_r"] if scored else None,
            "scored": scored,
            "win_rate": (
                item["wins"] / scored * 100.0
                if scored else None
            ),
        })

    return sorted(
        output,
        key=lambda item: (item["net_r"] if item["net_r"] is not None else -float("inf"), item["count"]),
        reverse=True,
    )


def _execution_breakdown(rows, key):
    groups = defaultdict(lambda: {"count": 0, "risks": []})
    for row in rows:
        name = str(row.get(key) if row.get(key) is not None else "Unspecified")
        groups[name]["count"] += 1
        risk = _finite(row.get("risk_r"))
        if risk is not None:
            groups[name]["risks"].append(risk)
    return [{"name": name, "count": item["count"],
             "risk_r": sum(item["risks"]) if item["risks"] else None,
             "unknown_risk": item["count"] - len(item["risks"])}
            for name, item in sorted(groups.items(), key=lambda p: (-p[1]["count"], p[0]))]


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
    scored = [item for item in items if item["net_r"] is not None]
    return min(scored, key=lambda item: (item["net_r"], -item["count"])) if scored else None


def _recommendation(stats):
    performance, process = stats["performance"], stats["process"]
    if process["risk_flags"]:
        return ("Review the recorded risk-protocol flags against your own planned allocation before the next entry; "
                "resolve any confirmed breach and keep risk inside that plan.")
    if stats.get("reflections", {}).get("off_plan_grades", 0) or process["violated"] or process["partial"]:
        return ("Before the next entry, write the setup criterion and planned invalidation, then check both against "
                "your own plan. Review the recorded off-plan/partial entries regardless of whether they made money.")
    if not performance["scored_trades"]:
        return ("If you traded, add the actual outcome and risk basis to the relevant Trade # when known. "
                "If you sat out, record that choice. There is no scored sample here to justify changing the plan.")
    if process["unknown"] or not process["journals"]:
        return ("Review one recorded trade against your written entry, stop and risk rules, and save whether you "
                "followed them. Profit/loss alone cannot establish process quality; keep planned risk unchanged.")
    weakest = stats.get("weakest_session")
    if weakest and weakest["net_r"] is not None and weakest["net_r"] < 0:
        return (f"Review one {weakest['name']} loss against your own plan and compare it with a followed-plan trade. "
                "Change only a repeatable, evidenced execution issue; this small sample does not establish an edge.")
    return ("Compare one scored trade with your written plan and repeat only the criteria you actually documented. "
            "Keep planned risk unchanged; a profitable sample does not prove that the process caused the result.")


def _reflection_evidence(rows):
    """Consume the optional canonical reflection module without changing data."""
    groups = defaultdict(lambda: {"trades": 0, "scored": 0, "net_r": 0.0})
    reported = 0
    try:
        from gbop_voice_web.trade_feelings import effective_history
    except ImportError:
        effective_history = lambda metadata: []
    for row in rows:
        metadata = row["metadata"]
        reports = effective_history(metadata)
        phrases = {(str(item.get("stage") or "unspecified"), str(item.get("feeling") or "").strip())
                   for item in reports if item.get("feeling")}
        # Pre-existing journal emotion is still an explicit self-report, not a diagnosis.
        if not phrases and isinstance(metadata.get("emotion"), str) and metadata["emotion"].strip():
            phrases.add(("unspecified", metadata["emotion"].strip()))
        if phrases:
            reported += 1
        for stage, feeling in phrases:
            group = groups[(stage, feeling)]
            group["trades"] += 1
            if row["final_result_r"] is not None:
                group["scored"] += 1
                group["net_r"] += row["final_result_r"]
    try:
        from gbop_voice_web.trade_self_grades import self_grade_counts
        grades = self_grade_counts([{**r, "result_r": r["final_result_r"]} for r in rows])
        grade_counts = {n: grades["counts"].get(f"type{n}", 0) for n in range(1, 5)}
    except ImportError:
        grade_counts = dict.fromkeys(range(1, 5), 0)
    return {"reported_trades": reported, "missing_trades": len(rows) - reported,
            "feelings": [{"stage": k[0], "feeling": k[1], **v} for k, v in
                         sorted(groups.items(), key=lambda item: (-item[1]["trades"], item[0]))],
            "grade_counts": dict(grade_counts), "graded_trades": sum(grade_counts.values()),
            "off_plan_grades": grade_counts[3] + grade_counts[4],
            "note": "Optional member reports only; groups may overlap. Association is not causation."}


def _patterns(stats):
    performance, process = stats["performance"], stats["process"]
    lines = []
    if performance["scored_trades"]:
        lines.append(f"{performance['scored_trades']} known R outcome(s): {format_r(performance['net_r'])}; "
                     f"{performance['unscored_trades']} unknown R. Descriptive sample, not a prediction.")
        scored_groups = [item for item in stats["by_play"] if item["win_rate"] is not None]
        if scored_groups:
            group = scored_groups[0]
            lines.append(f"{group['name']}: {format_r(group['net_r'])} across {group['count']} recorded trade(s).")
    else:
        lines.append("No known R outcomes in this window; missing records do not establish inactivity or nonadherence.")
    if process["followed"] + process["partial"] + process["violated"]:
        lines.append(f"Reported adherence: {process['followed']} followed, {process['partial']} partial, "
                     f"{process['violated']} violated; {process['unknown']} unknown.")
    else:
        lines.append("Plan adherence is unknown from the available trade records.")
    return lines


def format_comparison(stats):
    comparison, performance = stats.get("comparison"), stats["performance"]
    lines = []
    if comparison and performance["scored_trades"] and comparison["scored_trades"]:
        lines.append(f"Prior week: {format_r(comparison['net_r'])} across {comparison['scored_trades']} known R outcome(s). "
                     "Unequal/limited samples are not a skill trend.")
    if comparison and stats["reflections"]["graded_trades"] and comparison["reflections"]["graded_trades"]:
        current, previous = stats["reflections"], comparison["reflections"]
        lines.append(f"Optional off-plan self-grades: {current['off_plan_grades']}/{current['graded_trades']} this week, "
                     f"{previous['off_plan_grades']}/{previous['graded_trades']} prior week; self-report coverage may differ.")
    return "\n".join(lines)


def _market_scopes(rows, period):
    """Only member-recorded assets and attributable dates; never guess favorites."""
    scopes = set()
    unknown = 0
    for row in rows:
        meta = row["metadata"]
        stamp = _timestamp(meta.get("reported_exit_at") or (meta.get("reported_entry_at") if not meta.get("trade_date") else None))
        day = stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() if stamp else meta.get("trade_date")
        shift = period.get("shift") or {"Day Shift": "day", "Night Shift": "night"}.get(_normalize_session(row.get("session")))
        if not shift and stamp:
            hour = stamp.astimezone(ZoneInfo("America/New_York")).hour
            shift = "day" if 9 <= hour < 12 else "night" if hour >= 21 else None
        if row.get("asset") and day and shift:
            scopes.add((row["asset"], day, shift))
        else:
            unknown += 1
    return sorted(scopes), unknown


def collect_market_context(db_factory, rows, period, max_scopes=12):
    """Deterministic closed-candle context. No paid model, market call or writes."""
    scopes, unassigned = _market_scopes(rows, period)
    output = {"windows": [], "requested_windows": len(scopes), "omitted_windows": max(0, len(scopes)-max_scopes),
              "unassigned_trades": unassigned,
              "note": "Retained broker candles only; observed movement does not establish your entry, fill, opportunity or P/L."}
    if not scopes:
        output["message"] = "No attributable asset/date/shift in the recorded trades; market context is unverified."
        return output
    from gbop_voice_web.market_data import read_feed, history_bars
    from gbop_voice_web.shift_availability import shift_bounds, assess_shift
    for asset, day, shift in scopes[:max_scopes]:
        item = {"asset": asset, "date": day, "shift": shift}
        try:
            opening, end = shift_bounds(day, shift)
            # Cut off at the end of this review even if the retained feed is newer.
            cutoff = min(end, int(_timestamp(period["end_utc"]).timestamp()), int(datetime.now(timezone.utc).timestamp()))
            feed = read_feed(db_factory, asset, now=cutoff)
            if not feed.get("ok"):
                item.update(status="unavailable", message="No retained broker feed available.")
            else:
                bars, step = history_bars(db_factory, feed, opening-3600, end, day, shift, cutoff)
                coverage = assess_shift(bars, day, shift, step, cutoff)
                observed = [bar for bar in bars if opening <= bar["time"] and bar["time"] + step <= cutoff]
                item.update(status=coverage["review_scope"], coverage=coverage,
                            source=feed.get("source"), symbol=feed.get("symbol"))
                if observed:
                    item.update(first_open=observed[0]["open"], last_close=observed[-1]["close"],
                                observed_high=max(b["high"] for b in observed), observed_low=min(b["low"] for b in observed),
                                change_pct=(observed[-1]["close"]/observed[0]["open"]-1)*100,
                                through_utc=datetime.fromtimestamp(observed[-1]["time"]+step, timezone.utc).isoformat())
        except Exception:
            # Report missing coverage, never turn failed retrieval into market closure.
            item.update(status="unavailable", message="Retained candle coverage could not be verified.")
        output["windows"].append(item)
    return output


def collect_snapshot(db_factory, guild_id: int, user_id: int, period: dict):
    with db_factory() as conn:
        theses, all_records = _review_records(conn, guild_id, user_id)
        closed, excluded, bases = [], Counter(), Counter()
        for row in all_records:
            if row["kind"] != "trade":
                excluded["study_or_reflection"] += 1
                continue
            if row.get("status") in {"OPEN", "IDEA"} or row["metadata"].get("reported_outcome") == "open":
                continue
            selected, basis = _selected_date(row, period)
            if selected:
                row["date_basis"] = basis
                closed.append(row)
                bases[basis] += 1
            elif basis in {"undated", "invalid_date", "unassigned_shift"}:
                excluded[basis] += 1
        closed.sort(key=lambda r: (str(r["metadata"].get("reported_exit_at") or r["metadata"].get("trade_date")
                                      or r["metadata"].get("reported_entry_at") or r.get("closed_at") or ""), str(r["record_key"])))
        study_theses = {r["thesis_id"] for r in all_records if r["kind"] != "trade"}
        executions = [dict(r) for r in conn.execute(
            "SELECT * FROM thesis_executions WHERE guild_id=? AND user_id=? ORDER BY created_at,id",
            (guild_id, user_id)).fetchall()]
        owned_theses = {r["id"] for r in theses}
        executions = [r for r in executions if _in_window(r.get("created_at"), period)
                      and r.get("thesis_id") in owned_theses and r.get("thesis_id") not in study_theses]
        flags = [dict(r) for r in conn.execute(
            "SELECT * FROM risk_flags WHERE guild_id=? AND user_id=? ORDER BY created_at,id",
            (guild_id, user_id)).fetchall()]
        flags = [r for r in flags if _in_window(r.get("created_at"), period)
                 and (r.get("thesis_id") is None or r["thesis_id"] in owned_theses)
                 and r.get("thesis_id") not in study_theses]
        checkins = [dict(r) for r in conn.execute(
            "SELECT shift,response,responded_at,shift_date FROM post_shift_checkins WHERE guild_id=? AND user_id=? AND shift_date>=? AND shift_date<=? ORDER BY shift_date,id",
            (guild_id, user_id, period["start_day"].isoformat(), period["end_day"].isoformat())).fetchall()]
        if period.get("shift"):
            checkins = [r for r in checkins if r["shift"] == period["shift"]]
        plans = []
        if _table_exists(conn, "gbop_shift_plans"):
            plans = [dict(r) for r in conn.execute(
                "SELECT session_date,shift,plan FROM gbop_shift_plans WHERE guild_id=? AND user_id=? AND session_date>=? AND session_date<=? ORDER BY session_date,shift",
                (guild_id, user_id, period["start_day"].isoformat(), period["end_day"].isoformat())).fetchall()]
            if period.get("shift"):
                plans = [r for r in plans if r["shift"] == period["shift"]]
    scored = [r for r in closed if r["final_result_r"] is not None]
    results = [r["final_result_r"] for r in scored]
    wins, losses = [v for v in results if v > 0], [v for v in results if v < 0]
    gross_loss = abs(sum(losses))
    max_win_streak, max_loss_streak = _streaks(scored)
    adherence = Counter(_record_adherence(r) for r in closed)
    known = sum(adherence[k] for k in ("followed", "partial", "violated"))
    risks = [_finite(r.get("risk_r")) for r in executions]
    known_risks = [r for r in risks if r is not None]
    by_play, by_asset, by_session = (_trade_breakdown(closed, key) for key in ("play", "asset", "session"))
    stats = {
        "period": period,
        "performance": {
            "opened_trades": sum(r.get("status") not in {"JOURNALED", "IDEA"} and r["id"] not in study_theses
                                  and _in_window(r.get("created_at"), period) for r in theses),
            "closed_trades": len(closed), "scored_trades": len(scored), "unscored_trades": len(closed)-len(scored),
            "wins": len(wins), "losses": len(losses), "breakeven": sum(v == 0 for v in results),
            "win_rate": len(wins)/len(results)*100 if results else None,
            "net_r": sum(results) if results else None, "avg_r": sum(results)/len(results) if results else None,
            "avg_win_r": sum(wins)/len(wins) if wins else None, "avg_loss_r": sum(losses)/len(losses) if losses else None,
            "profit_factor": sum(wins)/gross_loss if gross_loss else (float("inf") if wins else None),
            "best_r": max(results) if results else None, "worst_r": min(results) if results else None,
            "max_win_streak": max_win_streak, "max_loss_streak": max_loss_streak,
            "open_now": sum(r.get("status") == "OPEN" and r["id"] not in study_theses for r in theses),
        },
        "execution": {"count": len(executions), "risk_r": sum(known_risks) if known_risks else None,
                      "known_risk_count": len(known_risks), "unknown_risk_count": len(risks)-len(known_risks),
                      "avg_risk_r": sum(known_risks)/len(known_risks) if known_risks else None,
                      "by_model": _execution_breakdown(executions, "entry_model"),
                      "by_tier": _execution_breakdown(executions, "tier")},
        "process": {"journals": sum(r["has_journal"] for r in closed),
                    **{k: adherence[k] for k in ("followed", "partial", "violated", "unknown")},
                    "full_adherence_rate": adherence["followed"]/known*100 if known else None,
                    "risk_flags": len(flags), "checkins_expected": len(checkins),
                    "checkins_completed": sum(bool((r.get("response") or "").strip()) for r in checkins)},
        "by_play": by_play, "by_asset": by_asset, "by_session": by_session,
        "top_play": _top_or_none(by_play), "top_asset": _top_or_none(by_asset),
        "strongest_session": _top_or_none(by_session), "weakest_session": _bottom_or_none(by_session),
        "reflections": _reflection_evidence(closed), "plans": plans,
        "coverage": {"date_bases": dict(bases), "excluded": dict(excluded),
                     "note": "Reported trade dates/times take priority. Legacy close logs and execution/risk logs are logging activity, not verified fill times. Missing check-ins do not mean nonadherence."},
    }
    if period["kind"] == "weekly":
        previous_period = weekly_period(period["end_day"] - timedelta(days=7), ZoneInfo("America/New_York"))
        previous = [r for r in all_records if r["kind"] == "trade" and r.get("status") not in {"OPEN", "IDEA"}
                    and r["metadata"].get("reported_outcome") != "open" and _selected_date(r, previous_period)[0]]
        prior_results = [r["final_result_r"] for r in previous if r["final_result_r"] is not None]
        stats["comparison"] = {"period": previous_period, "scored_trades": len(prior_results),
                               "net_r": sum(prior_results) if prior_results else None,
                               "reflections": _reflection_evidence(previous)}
    stats["patterns"] = _patterns(stats)
    stats["recommendation"] = _recommendation(stats)
    stats["market_context"] = collect_market_context(db_factory, closed, period)
    return stats


def format_reflections(reflections):
    lines = [f"Feelings recorded for {reflections['reported_trades']} trade(s); {reflections['missing_trades']} without feelings."]
    for item in reflections["feelings"][:3]:
        lines.append(f"{item['stage']}: “{item['feeling'][:100]}” • {item['trades']} trade(s), "
                     f"{format_r(item['net_r']) if item['scored'] else 'R unknown'} ({item['scored']} known R).")
    grades = reflections["grade_counts"]
    if reflections["graded_trades"]:
        lines.append("Self-grades: " + " • ".join(f"Type {n}: {grades.get(n, 0)}" for n in range(1, 5)))
        lines.append("Types 1/2: on-plan profit/predefined-stop loss; 3/4: off-plan loss/profit. "
                     f"{reflections['reported_trades'] + reflections['missing_trades'] - reflections['graded_trades']} ungraded; never inferred.")
    else:
        lines.append("No optional self-grades recorded; none inferred from R.")
    return "\n".join(lines + [reflections["note"]])[:1000]


def format_market_context(context):
    lines = []
    for item in context["windows"][:4]:
        name = f"{item['asset']} {item['date']} {item['shift']}"
        coverage = item.get("coverage")
        if "change_pct" in item:
            lines.append(f"{name}: {item['change_pct']:+.2f}% observed open→close; "
                         f"{coverage['closed_bar_count']}/{coverage['expected_bar_count']} closed bars, "
                         f"{item['status']} coverage ({coverage['source_resolution_seconds']}s source).")
        else:
            lines.append(f"{name}: coverage unavailable/unverified.")
    if not lines:
        lines.append(context.get("message", "Market context unverified."))
    extra = context["omitted_windows"] + max(0, len(context["windows"])-4)
    if extra:
        lines.append(f"{extra} additional asset/shift window(s) not shown in this compact review.")
    if context["unassigned_trades"]:
        lines.append(f"{context['unassigned_trades']} trade(s) lack an attributable market scope.")
    return "\n".join(lines + [context["note"]])[:1000]


def format_coverage(stats):
    coverage = stats["coverage"]
    bases, excluded = coverage["date_bases"], coverage["excluded"]
    return (f"Date basis: {bases.get('reported_time', 0)} reported timestamps, "
            f"{bases.get('reported_date', 0)} reported dates, {bases.get('close_log', 0)} legacy close logs. "
            f"Undated/unassigned history excluded: {sum(excluded.get(k, 0) for k in ('undated', 'invalid_date', 'unassigned_shift'))}. "
            f"Unknown R: {stats['performance']['unscored_trades']}; unknown adherence: {stats['process']['unknown']}. "
            + coverage["note"])[:1000]


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
            f"• {item['name'][:80]}: {format_r(item['net_r'])} | "
            f"{item['count']} trade(s), {item['scored']} known R{win_rate}"
        )

    return "\n".join(lines)


def format_execution_breakdown(items, limit=4):
    if not items:
        return "No executions"

    return "\n".join(
        f"• {item['name'][:80]}: {item['count']} | "
        f"{format_r(item['risk_r'])} known risk ({item['unknown_risk']} unknown)"
        for item in items[:limit]
    )
