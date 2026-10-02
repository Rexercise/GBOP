"""Member-plan-based active trade assistance for GBOP.

This module never supplies live market signals. It stores and recalls the member's
own objective, invalidation, management plan, and reported progress so GBOP can
keep the trader anchored to what they already decided.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from gbop_voice_web.trade_numbers import trade_number, trade_record_id
from gbop_voice_web.trade_photos import schema

TEXT_NULL = {"type": ["string", "null"]}
NUMBER_NULL = {"type": ["number", "null"]}
INT_NULL = {"type": ["integer", "null"]}

MANAGEMENT_EVENT = "GBOP_MANAGEMENT_PLAN"
PROGRESS_EVENT = "GBOP_PROGRESS"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _choose_open_trade(db, guild, user, displayed_trade_number=None):
    with db() as conn:
        if displayed_trade_number is not None:
            internal = trade_record_id(
                db, guild, user, int(displayed_trade_number)
            )
            if internal is None:
                return None, "That trade number was not found in your account."
            row = conn.execute(
                """SELECT * FROM theses
                WHERE id=? AND guild_id=? AND user_id=? AND status='OPEN'""",
                (internal, guild, user),
            ).fetchone()
            if row is None:
                return None, "That trade is not currently open."
            return row, None

        rows = conn.execute(
            """SELECT * FROM theses
            WHERE guild_id=? AND user_id=? AND status='OPEN'
            ORDER BY id DESC""",
            (guild, user),
        ).fetchall()

    if not rows:
        return None, "There are no open trades."
    if len(rows) > 1:
        return None, {
            "needs_trade_selection": True,
            "open_trades": [
                {
                    "trade_id": trade_number(db, guild, user, row["id"]),
                    "asset": row["asset"],
                    "direction": row["direction"],
                    "play": row["play"],
                }
                for row in rows[:10]
            ],
        }
    return rows[0], None


def _used_r(db, thesis_id):
    with db() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(risk_r),0) AS total
            FROM thesis_executions WHERE thesis_id=?""",
            (thesis_id,),
        ).fetchone()
    return float(row["total"] or 0.0)


def _decode_details(value):
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _latest_payload(db, thesis_id, event_name):
    with db() as conn:
        row = conn.execute(
            """SELECT details,result_r,created_at
            FROM thesis_events
            WHERE thesis_id=? AND event=?
            ORDER BY id DESC LIMIT 1""",
            (thesis_id, event_name),
        ).fetchone()
    if row is None:
        return None
    payload = _decode_details(row["details"])
    payload["event_result_r"] = row["result_r"]
    payload["created_at"] = row["created_at"]
    return payload


def _trade_state(db, guild, user, row):
    management = _latest_payload(db, row["id"], MANAGEMENT_EVENT)
    progress = _latest_payload(db, row["id"], PROGRESS_EVENT)
    return {
        "trade_id": trade_number(db, guild, user, row["id"]),
        "asset": row["asset"],
        "direction": row["direction"],
        "play": row["play"],
        "objective": row["objective"],
        "thesis_invalidation": row["thesis_invalidation"],
        "recorded_risk_r": _used_r(db, row["id"]),
        "management": management,
        "latest_progress": progress,
    }


def update_trade_plan(db, guild, user, args):
    row, error = _choose_open_trade(db, guild, user, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}

    objective = args.get("objective")
    invalidation = args.get("thesis_invalidation")
    management_plan = args.get("management_plan")
    trigger = args.get("protection_trigger_pct")

    if trigger is not None:
        trigger = float(trigger)
        if not math.isfinite(trigger) or trigger < 0 or trigger > 100:
            raise ValueError("protection_trigger_pct must be between 0 and 100.")

    if objective is not None and not str(objective).strip():
        raise ValueError("objective cannot be blank.")
    if invalidation is not None and not str(invalidation).strip():
        raise ValueError("thesis_invalidation cannot be blank.")
    if management_plan is not None and not str(management_plan).strip():
        raise ValueError("management_plan cannot be blank.")

    with db() as conn:
        if objective is not None:
            conn.execute(
                """UPDATE theses SET objective=?
                WHERE id=? AND guild_id=? AND user_id=?""",
                (str(objective).strip(), row["id"], guild, user),
            )
        if invalidation is not None:
            conn.execute(
                """UPDATE theses SET thesis_invalidation=?
                WHERE id=? AND guild_id=? AND user_id=?""",
                (str(invalidation).strip(), row["id"], guild, user),
            )

    previous = _latest_payload(db, row["id"], MANAGEMENT_EVENT) or {}
    if management_plan is not None or trigger is not None:
        effective_plan = (
            str(management_plan).strip()
            if management_plan is not None
            else previous.get("management_plan")
        )
        effective_trigger = (
            trigger
            if trigger is not None
            else previous.get("protection_trigger_pct")
        )
        payload = {
            "management_plan": effective_plan,
            "protection_trigger_pct": effective_trigger,
        }
        with db() as conn:
            conn.execute(
                """INSERT INTO thesis_events
                (thesis_id,guild_id,user_id,event,details,result_r,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    row["id"], guild, user, MANAGEMENT_EVENT,
                    json.dumps(payload, separators=(",", ":")),
                    None, _now(),
                ),
            )

    with db() as conn:
        refreshed = conn.execute(
            "SELECT * FROM theses WHERE id=?",
            (row["id"],),
        ).fetchone()

    return {
        "ok": True,
        "trade": _trade_state(db, guild, user, refreshed),
        "message": (
            "Saved the member's own trade-management plan. GBOP should use it "
            "as an anchor, not invent a new management rule."
        ),
    }


def record_trade_progress(db, guild, user, args):
    row, error = _choose_open_trade(db, guild, user, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}

    progress = args.get("progress_pct")
    current_r = args.get("current_result_r")
    note = args.get("note")

    if progress is not None:
        progress = float(progress)
        if not math.isfinite(progress) or progress < 0 or progress > 100:
            raise ValueError("progress_pct must be between 0 and 100.")
    if current_r is not None:
        current_r = float(current_r)
        if not math.isfinite(current_r):
            raise ValueError("current_result_r must be finite.")
    if note is not None:
        note = str(note).strip()

    if progress is None and current_r is None and not note:
        raise ValueError("Record at least progress_pct, current_result_r, or a note.")

    payload = {
        "progress_pct": progress,
        "current_result_r": current_r,
        "note": note or "",
    }
    with db() as conn:
        conn.execute(
            """INSERT INTO thesis_events
            (thesis_id,guild_id,user_id,event,details,result_r,created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (
                row["id"], guild, user, PROGRESS_EVENT,
                json.dumps(payload, separators=(",", ":")),
                current_r, _now(),
            ),
        )

    management = _latest_payload(db, row["id"], MANAGEMENT_EVENT)
    trigger = (
        management.get("protection_trigger_pct")
        if management else None
    )
    reached = bool(
        progress is not None
        and trigger is not None
        and float(progress) >= float(trigger)
    )

    return {
        "ok": True,
        "trade_id": trade_number(db, guild, user, row["id"]),
        "objective": row["objective"],
        "thesis_invalidation": row["thesis_invalidation"],
        "reported_progress_pct": progress,
        "reported_result_r": current_r,
        "management": management,
        "saved_trigger_reached": reached,
        "management_reminder": (
            management.get("management_plan")
            if reached and management and management.get("management_plan")
            else None
        ),
        "message": (
            "Use only the member-reported progress and their saved management "
            "plan. GBOP does not have a live price feed from this tool."
        ),
    }


def get_trade_assist(db, guild, user, args):
    row, error = _choose_open_trade(db, guild, user, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}
    return {
        "ok": True,
        "trade": _trade_state(db, guild, user, row),
        "instruction": (
            "Restate the member's own objective, invalidation and management "
            "plan. Do not create a new signal or pretend to know live price."
        ),
    }


def trade_assist_context(db, guild, user):
    try:
        with db() as conn:
            rows = conn.execute(
                """SELECT * FROM theses
                WHERE guild_id=? AND user_id=? AND status='OPEN'
                ORDER BY id DESC LIMIT 5""",
                (guild, user),
            ).fetchall()
        if not rows:
            return "ACTIVE TRADE ASSISTANCE: no open trades."

        lines = ["ACTIVE TRADE ASSISTANCE — MEMBER-SAVED PLAN"]
        for row in rows:
            state = _trade_state(db, guild, user, row)
            management = state.get("management") or {}
            progress = state.get("latest_progress") or {}
            lines.append(
                f"- Trade #{state['trade_id']} {state['asset']} {state['direction']} | "
                f"objective: {state['objective']} | invalidation: {state['thesis_invalidation']} | "
                f"management: {management.get('management_plan') or 'not saved'} | "
                f"trigger: {management.get('protection_trigger_pct') if management else 'not saved'}% | "
                f"last reported progress: {progress.get('progress_pct') if progress else 'unknown'}%."
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"ACTIVE TRADE ASSISTANCE: unavailable ({type(exc).__name__})."


TRADE_ASSIST_PROMPT = """
# ACTIVE TRADE ASSISTANCE

Trade assistance is member-plan based, not signal generation.
- When a member states or changes an objective, thesis invalidation, management
  plan, or personal protection trigger, save it with update_trade_plan.
- When a member reports progress such as "we're 80% to target", "+1.5R", or a
  management development, use record_trade_progress.
- If record_trade_progress says saved_trigger_reached=true, remind the member
  of THEIR saved management plan. Do not invent a stop, exit, partial, or risk rule.
- If no management plan is saved, say so rather than substituting a GTOP-wide
  rule or another member's rule.
- Use get_market_price for broker quotes when available; never describe stale or
  absent data as live. Objective completion remains member-reported unless exact
  saved price levels and reliable evidence support it. Never invent monitoring.
- For "what was my target?", "what's my invalidation?", or "what was my management
  plan?", use get_trade_assist.
""".strip()


TRADE_ASSIST_TOOLS = [
    schema(
        "update_trade_plan",
        "Save changes to an open trade's member-defined objective, thesis invalidation, management plan, or protection trigger. Null means unchanged.",
        {
            "trade_id": INT_NULL,
            "objective": TEXT_NULL,
            "thesis_invalidation": TEXT_NULL,
            "management_plan": TEXT_NULL,
            "protection_trigger_pct": NUMBER_NULL,
        },
    ),
    schema(
        "record_trade_progress",
        "Save member-reported progress on an open trade and check it against that member's saved management trigger. This is not a live price feed.",
        {
            "trade_id": INT_NULL,
            "progress_pct": NUMBER_NULL,
            "current_result_r": NUMBER_NULL,
            "note": TEXT_NULL,
        },
    ),
    schema(
        "get_trade_assist",
        "Read an open trade's saved target, invalidation, member-defined management plan, and last reported progress.",
        {"trade_id": INT_NULL},
    ),
]

TRADE_ASSIST_NAMES = {tool["name"] for tool in TRADE_ASSIST_TOOLS}


def trade_assist_tool(db, guild, user, name, args):
    handlers = {
        "update_trade_plan": update_trade_plan,
        "record_trade_progress": record_trade_progress,
        "get_trade_assist": get_trade_assist,
    }
    fn = handlers.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown trade-assist tool: {name}"}
    try:
        return fn(db, guild, user, args)
    except (ValueError, TypeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
