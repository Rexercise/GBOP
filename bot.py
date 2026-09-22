import os
import logging
from array import array
import sys
import hashlib
import base64
import wave
import time
import threading
import re
import json
import asyncio
from db_compat import db
from datetime import datetime, timezone

import discord
from discord.ext import voice_recv
from discord import app_commands
from dotenv import load_dotenv
from openai import OpenAI
import websockets

load_dotenv()

# Render captures stdout; flush each line so startup progress is visible.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
logger = logging.getLogger("gbop")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GTOP_GUILD_ID = int(os.getenv("GTOP_GUILD_ID"))
GTOP_OWNER_USER_ID = int(os.getenv("GTOP_OWNER_USER_ID"))
GTOP_MEMBER_ROLE_ID = int(os.getenv("GTOP_MEMBER_ROLE_ID"))

_admin_role = os.getenv("GTOP_ADMIN_ROLE_ID", "").strip()
GTOP_ADMIN_ROLE_ID = int(_admin_role) if _admin_role else None

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from .env")

GUILD = discord.Object(id=GTOP_GUILD_ID)
DB_PATH = "Supabase PostgreSQL"

intents = discord.Intents.default()
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# -----------------------------
# DATABASE
# -----------------------------




def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                activated INTEGER NOT NULL DEFAULT 0,
                leadership_ack INTEGER NOT NULL DEFAULT 0,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)


def ensure_member_record(member: discord.Member):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE guild_id=? AND user_id=?",
            (GTOP_GUILD_ID, member.id)
        ).fetchone()

        if row is None:
            stamp = now()
            conn.execute("""
                INSERT INTO members (
                    guild_id, user_id, username, display_name,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                GTOP_GUILD_ID,
                member.id,
                str(member),
                member.display_name,
                stamp,
                stamp
            ))
        else:
            conn.execute("""
                UPDATE members
                SET username=?, display_name=?, updated_at=?
                WHERE guild_id=? AND user_id=?
            """, (
                str(member),
                member.display_name,
                now(),
                GTOP_GUILD_ID,
                member.id
            ))


def get_member_record(user_id: int):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM members WHERE guild_id=? AND user_id=?",
            (GTOP_GUILD_ID, user_id)
        ).fetchone()


# -----------------------------
# ACCESS CONTROL
# -----------------------------

def role_ids(member):
    return {role.id for role in getattr(member, "roles", [])}


def is_owner(member):
    return member.id == GTOP_OWNER_USER_ID


def is_admin(member):
    if is_owner(member):
        return True

    if GTOP_ADMIN_ROLE_ID is None:
        return False

    return GTOP_ADMIN_ROLE_ID in role_ids(member)


def has_member_role(member):
    return GTOP_MEMBER_ROLE_ID in role_ids(member)


async def require_member(interaction: discord.Interaction):
    member = interaction.user

    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "GBOP commands must be used inside the G.T.O.P server.",
            ephemeral=True
        )
        return False

    ensure_member_record(member)

    if is_owner(member):
        return True

    if not has_member_role(member):
        await interaction.response.send_message(
            "❌ You do not currently have the GTOP member role required to use GBOP.",
            ephemeral=True
        )
        return False

    record = get_member_record(member.id)

    if record["revoked"]:
        await interaction.response.send_message(
            "⛔ Your GBOP access is currently revoked.",
            ephemeral=True
        )
        return False

    if not record["activated"]:
        await interaction.response.send_message(
            "You need to activate your GBOP profile first.\nUse `/activate agree:true`.",
            ephemeral=True
        )
        return False

    return True


async def require_admin(interaction: discord.Interaction):
    member = interaction.user

    if not isinstance(member, discord.Member) or not is_admin(member):
        await interaction.response.send_message(
            "⛔ GTOP leadership access required.",
            ephemeral=True
        )
        return False

    return True



# -----------------------------
# JOURNAL V1
# -----------------------------

def init_journal_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS journals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                rule_adherence TEXT,
                result_r REAL,
                study_note TEXT,
                created_at TEXT NOT NULL
            )
        """)


def format_r(value):
    if value is None:
        return "Not specified"
    return f"{float(value):+.2f}R"


# JOURNAL MODAL V2

class JournalModal(discord.ui.Modal, title="GBOP Trade Journal"):

    description = discord.ui.TextInput(
        label="Trade / Session Summary",
        placeholder="Asset, play, what happened, entry/management...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    rule_adherence = discord.ui.TextInput(
        label="Rule Adherence",
        placeholder="Followed / Partial deviation / Violated",
        required=True,
        max_length=100
    )

    result_r = discord.ui.TextInput(
        label="Result in R",
        placeholder="Example: +2.50, -0.25, 0",
        required=False,
        max_length=20
    )

    study_note = discord.ui.TextInput(
        label="Study Note",
        placeholder="One pattern to preserve and one adjustment for next time.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=600
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_member(interaction):
            return

        raw_result = str(self.result_r).strip()
        result_value = None

        if raw_result:
            cleaned = raw_result.upper().replace("R", "").strip()

            try:
                result_value = float(cleaned)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Result in R must look like `+2.5`, `-0.25`, or `0`.",
                    ephemeral=True
                )
                return

        with db() as conn:
            cur = conn.execute("""
                INSERT INTO journals (
                    guild_id,
                    user_id,
                    description,
                    rule_adherence,
                    result_r,
                    study_note,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                GTOP_GUILD_ID,
                interaction.user.id,
                str(self.description).strip(),
                str(self.rule_adherence).strip(),
                result_value,
                str(self.study_note).strip(),
                now()
            ))

            journal_id = cur.lastrowid

        await interaction.response.send_message(
            f"📓 **Private journal #{journal_id} saved.**\n"
            f"Result: **{format_r(result_value)}**\n"
            f"Rule Adherence: **{self.rule_adherence}**\n"
            f"Study Note: {self.study_note}",
            ephemeral=True
        )


@tree.command(
    name="journal",
    description="View your recent private GTOP journal entries.",
    guild=GUILD,
)
async def journal(interaction: discord.Interaction):
    await journals.callback(interaction, limit=5)

@tree.command(
    name="journals",
    description="View your recent private GTOP journal entries.",
    guild=GUILD,
)
@app_commands.describe(limit="Number of recent entries to show, 1-10")
async def journals(
    interaction: discord.Interaction,
    limit: int = 5
):
    if not await require_member(interaction):
        return

    limit = max(1, min(limit, 10))

    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM journals
            WHERE guild_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            GTOP_GUILD_ID,
            interaction.user.id,
            limit
        )).fetchall()

    if not rows:
        await interaction.response.send_message(
            "You do not have any journal entries yet.",
            ephemeral=True
        )
        return

    parts = ["**Your Recent GBOP Journal Entries**"]

    for row in rows:
        parts.append(
            f"\n**Journal #{row['id']}**"
            f"\nResult: {format_r(row['result_r'])}"
            f"\nAdherence: {row['rule_adherence'] or 'Not specified'}"
            f"\nEntry: {row['description'][:450]}"
            f"\nStudy Note: {row['study_note'] or 'Not specified'}"
        )

    await interaction.response.send_message(
        "\n".join(parts)[:1900],
        ephemeral=True
    )



# -----------------------------
# THESIS ENGINE V1
# -----------------------------

def init_thesis_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS theses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                play TEXT NOT NULL,
                session TEXT,
                crt_variant TEXT,
                htf_context TEXT,
                liquidity_purged TEXT,
                objective TEXT NOT NULL,
                thesis_invalidation TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                max_r REAL NOT NULL DEFAULT 1.0,
                final_result_r REAL,
                close_note TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                entry_model TEXT NOT NULL,
                tier INTEGER NOT NULL,
                risk_r REAL NOT NULL,
                entry_invalidation TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(thesis_id) REFERENCES theses(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS thesis_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                event TEXT NOT NULL,
                details TEXT,
                result_r REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(thesis_id) REFERENCES theses(id)
            )
        """)


def get_thesis(thesis_id: int, user_id: int):
    with db() as conn:
        return conn.execute("""
            SELECT *
            FROM theses
            WHERE id=? AND guild_id=? AND user_id=?
        """, (
            thesis_id,
            GTOP_GUILD_ID,
            user_id
        )).fetchone()

def trade_number_for_id(user_id: int, thesis_id: int):
    with db() as conn:
        rows = conn.execute("""
            SELECT id
            FROM theses
            WHERE guild_id=? AND user_id=?
            ORDER BY id ASC
        """, (
            GTOP_GUILD_ID,
            user_id
        )).fetchall()

    for number, row in enumerate(rows, start=1):
        if row["id"] == thesis_id:
            return number

    return None


def trade_id_from_number(user_id: int, trade_number: int):
    if trade_number <= 0:
        return None

    with db() as conn:
        rows = conn.execute("""
            SELECT id
            FROM theses
            WHERE guild_id=? AND user_id=?
            ORDER BY id ASC
        """, (
            GTOP_GUILD_ID,
            user_id
        )).fetchall()

    if trade_number > len(rows):
        return None

    return rows[trade_number - 1]["id"]


def next_trade_number(user_id: int):
    with db() as conn:
        count = conn.execute("""
            SELECT COUNT(*)
            FROM theses
            WHERE guild_id=? AND user_id=?
        """, (
            GTOP_GUILD_ID,
            user_id
        )).fetchone()[0]

    return int(count) + 1
def thesis_used_r(thesis_id: int):
    with db() as conn:
        value = conn.execute("""
            SELECT COALESCE(SUM(risk_r), 0)
            FROM thesis_executions
            WHERE thesis_id=? AND guild_id=?
        """, (
            thesis_id,
            GTOP_GUILD_ID
        )).fetchone()[0]

    return float(value or 0)


def thesis_remaining_r(thesis_id: int):
    return max(0.0, 1.0 - thesis_used_r(thesis_id))


def tier_max_r(tier: int):
    limits = {
        1: 1.00,
        2: 0.50,
        3: 0.33
    }
    return limits.get(tier)


thesis = app_commands.Group(
    name="thesis",
    description="GTOP thesis and risk-budget controls."
)


@thesis.command(
    name="open",
    description="Open a new GTOP directional thesis."
)
@app_commands.describe(
    asset="Example: NAS100, Gold, Silver, Oil",
    direction="Bullish or Bearish",
    play="GTOP play controlling the opportunity",
    session="Example: Day Shift or Night Shift",
    objective="Predetermined drawn liquidity/objective",
    thesis_invalidation="What structurally invalidates the entire thesis",
    crt_variant="Optional CRT variant, example V2 or V6",
    htf_context="Optional higher-timeframe narrative",
    liquidity_purged="Optional liquidity already purged"
)
@app_commands.choices(
    direction=[
        app_commands.Choice(name="Bullish", value="Bullish"),
        app_commands.Choice(name="Bearish", value="Bearish"),
    ],
    play=[
        app_commands.Choice(name="9ate8", value="9ate8"),
        app_commands.Choice(name="Monday Range", value="Monday Range"),
        app_commands.Choice(name="Golden Candle Time (GCT)", value="GCT"),
        app_commands.Choice(name="Super Soup", value="Super Soup"),
        app_commands.Choice(name="Blessed Thief", value="Blessed Thief"),
        app_commands.Choice(name="CBDR", value="CBDR"),
    ]
)
async def thesis_open(
    interaction: discord.Interaction,
    asset: str,
    direction: app_commands.Choice[str],
    play: app_commands.Choice[str],
    session: str,
    objective: str,
    thesis_invalidation: str,
    crt_variant: str = "",
    htf_context: str = "",
    liquidity_purged: str = ""
):
    if not await require_member(interaction):
        return

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO theses (
                guild_id,
                user_id,
                asset,
                direction,
                play,
                session,
                crt_variant,
                htf_context,
                liquidity_purged,
                objective,
                thesis_invalidation,
                status,
                max_r,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 1.0, ?)
        """, (
            GTOP_GUILD_ID,
            interaction.user.id,
            asset.strip(),
            direction.value,
            play.value,
            session.strip(),
            crt_variant.strip(),
            htf_context.strip(),
            liquidity_purged.strip(),
            objective.strip(),
            thesis_invalidation.strip(),
            now()
        ))

        thesis_id = cur.lastrowid

    await interaction.response.send_message(
        f"🧠 **GTOP Thesis #{thesis_id} opened.**\n"
        f"Asset: **{asset}**\n"
        f"Direction: **{direction.value}**\n"
        f"Play: **{play.value}**\n"
        f"Objective: **{objective}**\n"
        f"Risk Budget: **1.00R**\n"
        f"Remaining: **1.00R**",
        ephemeral=True
    )


@thesis.command(
    name="status",
    description="View the current state of a GTOP thesis."
)
async def thesis_status(
    interaction: discord.Interaction,
    thesis_id: int
):
    if not await require_member(interaction):
        return

    row = get_thesis(thesis_id, interaction.user.id)

    if row is None:
        await interaction.response.send_message(
            "❌ Thesis not found.",
            ephemeral=True
        )
        return

    used = thesis_used_r(thesis_id)
    remaining = thesis_remaining_r(thesis_id)

    await interaction.response.send_message(
        f"**GTOP Thesis #{row['id']}**\n"
        f"Asset: **{row['asset']}**\n"
        f"Direction: **{row['direction']}**\n"
        f"Play: **{row['play']}**\n"
        f"Session: **{row['session'] or 'Not specified'}**\n"
        f"CRT Variant: **{row['crt_variant'] or 'Not specified'}**\n"
        f"Objective: **{row['objective']}**\n"
        f"Status: **{row['status']}**\n\n"
        f"Risk Used: **{used:.2f}R**\n"
        f"Risk Remaining: **{remaining:.2f}R**\n\n"
        f"Thesis Invalidation: {row['thesis_invalidation']}",
        ephemeral=True
    )


# -----------------------------
# EXECUTION ENGINE V2
# -----------------------------

def init_risk_flags_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id INTEGER NOT NULL,
                execution_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rule_code TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def record_risk_flag(
    thesis_id: int,
    execution_id: int,
    user_id: int,
    rule_code: str,
    message: str
):
    with db() as conn:
        conn.execute("""
            INSERT INTO risk_flags (
                thesis_id,
                execution_id,
                guild_id,
                user_id,
                rule_code,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            thesis_id,
            execution_id,
            GTOP_GUILD_ID,
            user_id,
            rule_code,
            message,
            now()
        ))


execution = app_commands.Group(
    name="execution",
    description="Record and review executions inside a GTOP thesis."
)


@execution.command(
    name="log",
    description="Record an actual execution inside a GTOP thesis."
)
@app_commands.describe(
    thesis_id="Thesis this execution belongs to",
    entry_model="Recognized GTOP execution model",
    tier="GTOP Risk Protocol tier",
    risk_r="Actual risk used on the execution",
    entry_invalidation="What invalidates this specific execution",
    note="Optional execution note"
)
@app_commands.choices(
    entry_model=[
        app_commands.Choice(name="Model 1 / CSD", value="Model 1"),
        app_commands.Choice(name="Turtle Wick Soup", value="Turtle Wick Soup"),
        app_commands.Choice(name="Turtle Body Soup", value="Turtle Body Soup"),
        app_commands.Choice(name="Super Soup", value="Super Soup"),
        app_commands.Choice(name="Blessed Thief", value="Blessed Thief"),
        app_commands.Choice(name="SMT Refinement", value="SMT"),
        app_commands.Choice(name="88.7 / OTE Refinement", value="88.7 OTE"),
    ],
    tier=[
        app_commands.Choice(name="Tier 1 — Confirmed", value=1),
        app_commands.Choice(name="Tier 2 — Early Confirmation", value=2),
        app_commands.Choice(name="Tier 3 — Anticipatory / Risk Entry", value=3),
    ]
)
async def execution_log(
    interaction: discord.Interaction,
    thesis_id: int,
    entry_model: app_commands.Choice[str],
    tier: app_commands.Choice[int],
    risk_r: float,
    entry_invalidation: str = "",
    note: str = ""
):
    if not await require_member(interaction):
        return

    thesis_row = get_thesis(thesis_id, interaction.user.id)

    if thesis_row is None:
        await interaction.response.send_message(
            "❌ Thesis not found.",
            ephemeral=True
        )
        return

    if thesis_row["status"] != "OPEN":
        await interaction.response.send_message(
            f"❌ Thesis #{thesis_id} is **{thesis_row['status']}** and cannot accept new executions.",
            ephemeral=True
        )
        return

    if risk_r <= 0:
        await interaction.response.send_message(
            "❌ Risk must be greater than 0R.",
            ephemeral=True
        )
        return

    used_before = thesis_used_r(thesis_id)
    projected_total = used_before + risk_r
    tier_limit = tier_max_r(tier.value)

    warnings = []

    if risk_r > tier_limit + 0.0001:
        warnings.append((
            "TIER_ALLOCATION",
            f"Tier {tier.value} maximum is {tier_limit:.2f}R, "
            f"but {risk_r:.2f}R was used."
        ))

    if projected_total > 1.0 + 0.0001:
        warnings.append((
            "THESIS_BUDGET",
            f"Thesis risk budget is 1.00R. "
            f"This execution brings total recorded risk to {projected_total:.2f}R."
        ))

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO thesis_executions (
                thesis_id,
                guild_id,
                user_id,
                entry_model,
                tier,
                risk_r,
                entry_invalidation,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            thesis_id,
            GTOP_GUILD_ID,
            interaction.user.id,
            entry_model.value,
            tier.value,
            risk_r,
            entry_invalidation.strip(),
            note.strip(),
            now()
        ))

        execution_id = cur.lastrowid

    for rule_code, message in warnings:
        record_risk_flag(
            thesis_id,
            execution_id,
            interaction.user.id,
            rule_code,
            message
        )

    used_after = thesis_used_r(thesis_id)
    remaining = thesis_remaining_r(thesis_id)
    over_budget = max(0.0, used_after - 1.0)

    if warnings:
        warning_text = "\n".join(
            f"⚠️ {message}" for _, message in warnings
        )

        response = (
            f"📝 **Execution #{execution_id} recorded.**\n"
            f"Thesis: **#{thesis_id}**\n"
            f"Entry Model: **{entry_model.value}**\n"
            f"Tier: **{tier.value}**\n"
            f"Actual Risk: **{risk_r:.2f}R**\n\n"
            f"**GTOP Risk Warning**\n"
            f"{warning_text}\n\n"
            f"Execution was **saved and flagged for review**.\n"
            f"Total Thesis Risk Recorded: **{used_after:.2f}R**\n"
            f"Remaining Protocol Budget: **{remaining:.2f}R**"
        )

        if over_budget > 0:
            response += (
                f"\nAmount Over 1R Budget: **{over_budget:.2f}R**"
            )

    else:
        response = (
            f"✅ **Execution #{execution_id} recorded within protocol.**\n"
            f"Thesis: **#{thesis_id}**\n"
            f"Entry Model: **{entry_model.value}**\n"
            f"Tier: **{tier.value}**\n"
            f"Actual Risk: **{risk_r:.2f}R**\n\n"
            f"Total Thesis Risk Recorded: **{used_after:.2f}R**\n"
            f"Remaining Thesis Ammunition: **{remaining:.2f}R**"
        )

    await interaction.response.send_message(
        response,
        ephemeral=True
    )


@execution.command(
    name="list",
    description="View executions recorded under a GTOP thesis."
)
@app_commands.describe(
    thesis_id="Thesis to review",
    limit="Number of recent executions to show, 1-10"
)
async def execution_list(
    interaction: discord.Interaction,
    thesis_id: int,
    limit: int = 10
):
    if not await require_member(interaction):
        return

    thesis_row = get_thesis(thesis_id, interaction.user.id)

    if thesis_row is None:
        await interaction.response.send_message(
            "❌ Thesis not found.",
            ephemeral=True
        )
        return

    limit = max(1, min(limit, 10))

    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM thesis_executions
            WHERE thesis_id=? AND guild_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            thesis_id,
            GTOP_GUILD_ID,
            interaction.user.id,
            limit
        )).fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No executions recorded for Thesis #{thesis_id}.",
            ephemeral=True
        )
        return

    parts = [
        f"**Executions — Thesis #{thesis_id}**"
    ]

    with db() as conn:
        for row in rows:
            flags = conn.execute("""
                SELECT message
                FROM risk_flags
                WHERE execution_id=?
                ORDER BY id ASC
            """, (row["id"],)).fetchall()

            flag_text = (
                " ⚠️ FLAGGED"
                if flags
                else " ✅"
            )

            parts.append(
                f"\n**Execution #{row['id']}**{flag_text}"
                f"\nModel: {row['entry_model']}"
                f"\nTier: {row['tier']}"
                f"\nRisk: {row['risk_r']:.2f}R"
                f"\nInvalidation: {row['entry_invalidation'] or 'Not specified'}"
            )

            for flag in flags:
                parts.append(
                    f"\n⚠️ {flag['message']}"
                )

    used = thesis_used_r(thesis_id)
    remaining = thesis_remaining_r(thesis_id)

    parts.append(
        f"\n\n**Recorded Thesis Risk:** {used:.2f}R"
        f"\n**Remaining Protocol Budget:** {remaining:.2f}R"
    )

    await interaction.response.send_message(
        "\n".join(parts)[:1900],
        ephemeral=True
    )


@thesis.command(
    name="event",
    description="Record a development without resetting thesis risk."
)
@app_commands.describe(
    event="Example: Entry stopped, Partial taken, Profit protection",
    details="What happened",
    result_r="Optional realized result from this event"
)
async def thesis_event(
    interaction: discord.Interaction,
    thesis_id: int,
    event: str,
    details: str = "",
    result_r: float | None = None
):
    if not await require_member(interaction):
        return

    row = get_thesis(thesis_id, interaction.user.id)

    if row is None:
        await interaction.response.send_message(
            "❌ Thesis not found.",
            ephemeral=True
        )
        return

    with db() as conn:
        conn.execute("""
            INSERT INTO thesis_events (
                thesis_id,
                guild_id,
                user_id,
                event,
                details,
                result_r,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            thesis_id,
            GTOP_GUILD_ID,
            interaction.user.id,
            event.strip(),
            details.strip(),
            result_r,
            now()
        ))

    remaining = thesis_remaining_r(thesis_id)

    await interaction.response.send_message(
        f"📝 **Thesis event recorded.**\n"
        f"Thesis: **#{thesis_id}**\n"
        f"Event: **{event}**\n"
        f"Remaining thesis ammunition: **{remaining:.2f}R**\n\n"
        f"Risk budget was **not reset**.",
        ephemeral=True
    )


@thesis.command(
    name="close",
    description="Close a completed GTOP thesis."
)
async def thesis_close(
    interaction: discord.Interaction,
    thesis_id: int,
    final_result_r: float | None = None,
    note: str = ""
):
    if not await require_member(interaction):
        return

    row = get_thesis(thesis_id, interaction.user.id)

    if row is None:
        await interaction.response.send_message(
            "❌ Thesis not found.",
            ephemeral=True
        )
        return

    with db() as conn:
        conn.execute("""
            UPDATE theses
            SET status='CLOSED',
                final_result_r=?,
                close_note=?,
                closed_at=?
            WHERE id=? AND guild_id=? AND user_id=?
        """, (
            final_result_r,
            note.strip(),
            now(),
            thesis_id,
            GTOP_GUILD_ID,
            interaction.user.id
        ))

    await interaction.response.send_message(
        f"✅ **Thesis #{thesis_id} closed.**\n"
        f"Final Result: **{format_r(final_result_r)}**",
        ephemeral=True
    )


@thesis.command(
    name="invalidate",
    description="Mark the entire directional thesis as structurally invalid."
)
async def thesis_invalidate(
    interaction: discord.Interaction,
    thesis_id: int,
    reason: str
):
    if not await require_member(interaction):
        return

    row = get_thesis(thesis_id, interaction.user.id)

    if row is None:
        await interaction.response.send_message(
            "❌ Thesis not found.",
            ephemeral=True
        )
        return

    with db() as conn:
        conn.execute("""
            UPDATE theses
            SET status='INVALIDATED',
                close_note=?,
                closed_at=?
            WHERE id=? AND guild_id=? AND user_id=?
        """, (
            reason.strip(),
            now(),
            thesis_id,
            GTOP_GUILD_ID,
            interaction.user.id
        ))

    await interaction.response.send_message(
        f"⛔ **Thesis #{thesis_id} invalidated.**\n"
        f"Reason: {reason}",
        ephemeral=True
    )


tree.add_command(thesis, guild=GUILD)
tree.add_command(execution, guild=GUILD)



# -----------------------------
# MEMBER TRADE FLOW V2
# -----------------------------

def init_member_trade_flow_db():
    with db() as conn:
        # Link journal records directly to their trade/thesis.
        journal_cols = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(journals)"
            ).fetchall()
        }

        if "thesis_id" not in journal_cols:
            conn.execute(
                "ALTER TABLE journals ADD COLUMN thesis_id INTEGER"
            )

        # Store protocol violations without preventing journaling.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id INTEGER NOT NULL,
                execution_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rule_code TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def member_risk_flag(
    thesis_id,
    execution_id,
    user_id,
    rule_code,
    message
):
    with db() as conn:
        conn.execute("""
            INSERT INTO risk_flags (
                thesis_id,
                execution_id,
                guild_id,
                user_id,
                rule_code,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            thesis_id,
            execution_id,
            GTOP_GUILD_ID,
            user_id,
            rule_code,
            message,
            now()
        ))


def open_member_trades(user_id):
    with db() as conn:
        return conn.execute("""
            SELECT *
            FROM theses
            WHERE guild_id=?
              AND user_id=?
              AND status='OPEN'
            ORDER BY id DESC
        """, (
            GTOP_GUILD_ID,
            user_id
        )).fetchall()


async def save_member_trade(
    interaction,
    asset,
    direction,
    play,
    entry_model,
    tier,
    risk_r
):
    # Create thesis behind the scenes.
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO theses (
                guild_id,
                user_id,
                asset,
                direction,
                play,
                session,
                crt_variant,
                htf_context,
                liquidity_purged,
                objective,
                thesis_invalidation,
                status,
                max_r,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, '',
                '', '', '',
                'Not specified at entry',
                'Not specified at entry',
                'OPEN', 1.0, ?
            )
        """, (
            GTOP_GUILD_ID,
            interaction.user.id,
            asset.strip(),
            direction,
            play,
            now()
        ))

        trade_id = cur.lastrowid

        # Create Execution #1 automatically.
        cur = conn.execute("""
            INSERT INTO thesis_executions (
                thesis_id,
                guild_id,
                user_id,
                entry_model,
                tier,
                risk_r,
                entry_invalidation,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
        """, (
            trade_id,
            GTOP_GUILD_ID,
            interaction.user.id,
            entry_model.strip(),
            tier,
            risk_r,
            now()
        ))

        execution_id = cur.lastrowid
    trade_number = trade_number_for_id(interaction.user.id, trade_id)
    warnings = []

    tier_limit = tier_max_r(tier)

    if risk_r > tier_limit + 0.0001:
        warnings.append((
            "TIER_ALLOCATION",
            f"Tier {tier} guideline maximum is "
            f"{tier_limit:.2f}R, but {risk_r:.2f}R was used."
        ))

    if risk_r > 1.0 + 0.0001:
        warnings.append((
            "THESIS_BUDGET",
            f"The GTOP thesis budget is 1.00R, "
            f"but {risk_r:.2f}R was used."
        ))

    for code_name, message in warnings:
        member_risk_flag(
            trade_id,
            execution_id,
            interaction.user.id,
            code_name,
            message
        )

    warning_text = ""

    if warnings:
        warning_text = (
            "\n\n**⚠️ GTOP Risk Review**\n"
            + "\n".join(
                f"• {message}"
                for _, message in warnings
            )
            + "\n\nThe execution was **saved and flagged**, not blocked."
        )

    await interaction.response.send_message(
        f"🏆 **Trade #{trade_number} opened.**\n"
        f"Asset: **{asset}**\n"
        f"Direction: **{direction}**\n"
        f"Play: **{play}**\n"
        f"Entry Model: **{entry_model}**\n"
        f"Tier: **{tier}**\n"
        f"Actual Risk: **{risk_r:.2f}R**"
        f"{warning_text}\n\n"
        f"Trade normally. When you're finished, use **`/close`**.",
        ephemeral=True
    )


class TradeOtherDetailsModal(
    discord.ui.Modal,
    title="GTOP Trade Details"
):
    def __init__(
        self,
        asset,
        direction,
        play,
        entry_model,
        tier,
        risk_r
    ):
        super().__init__()

        self.asset_value = asset
        self.direction_value = direction
        self.play_value = play
        self.entry_model_value = entry_model
        self.tier_value = tier
        self.risk_value = risk_r

        self.custom_play_input = None
        self.custom_entry_input = None

        if play == "OTHER":
            self.custom_play_input = discord.ui.TextInput(
                label="Play / Setup Used",
                placeholder="Example: H4 setup, Daily Range, custom model...",
                required=True,
                max_length=150
            )
            self.add_item(self.custom_play_input)

        if entry_model == "OTHER":
            self.custom_entry_input = discord.ui.TextInput(
                label="Entry Model Used",
                placeholder="Enter the actual entry model used...",
                required=True,
                max_length=150
            )
            self.add_item(self.custom_entry_input)

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        play_name = self.play_value
        model_name = self.entry_model_value

        if self.custom_play_input is not None:
            play_name = str(
                self.custom_play_input
            ).strip()

        if self.custom_entry_input is not None:
            model_name = str(
                self.custom_entry_input
            ).strip()

        await save_member_trade(
            interaction,
            self.asset_value,
            self.direction_value,
            play_name,
            model_name,
            self.tier_value,
            self.risk_value
        )


@tree.command(
    name="trade",
    description="Start and log a GTOP trade.",
    guild=GUILD
)
@app_commands.describe(
    asset="Asset being traded",
    direction="Directional thesis",
    play="GTOP play",
    entry_model="Entry model actually used",
    tier="GTOP Risk Protocol tier",
    risk_r="Actual risk used in R"
)
@app_commands.choices(
    direction=[
        app_commands.Choice(
            name="Bullish",
            value="Bullish"
        ),
        app_commands.Choice(
            name="Bearish",
            value="Bearish"
        ),
    ],
    play=[
        app_commands.Choice(
            name="9ate8",
            value="9ate8"
        ),
        app_commands.Choice(
            name="Monday Range",
            value="Monday Range"
        ),
        app_commands.Choice(
            name="Golden Candle Time (GCT)",
            value="GCT"
        ),
        app_commands.Choice(
            name="Super Soup",
            value="Super Soup"
        ),
        app_commands.Choice(
            name="Blessed Thief",
            value="Blessed Thief"
        ),
        app_commands.Choice(
            name="CBDR",
            value="CBDR"
        ),
        app_commands.Choice(
            name="Other",
            value="OTHER"
        ),
    ],
    entry_model=[
        app_commands.Choice(
            name="Model 1 / CSD",
            value="Model 1"
        ),
        app_commands.Choice(
            name="Turtle Wick Soup",
            value="Turtle Wick Soup"
        ),
        app_commands.Choice(
            name="Turtle Body Soup",
            value="Turtle Body Soup"
        ),
        app_commands.Choice(
            name="Super Soup",
            value="Super Soup"
        ),
        app_commands.Choice(
            name="Blessed Thief",
            value="Blessed Thief"
        ),
        app_commands.Choice(
            name="SMT Refinement",
            value="SMT"
        ),
        app_commands.Choice(
            name="88.7 / OTE Refinement",
            value="88.7 OTE"
        ),
        app_commands.Choice(
            name="Other",
            value="OTHER"
        ),
    ],
    tier=[
        app_commands.Choice(
            name="Tier 1 — Confirmed",
            value=1
        ),
        app_commands.Choice(
            name="Tier 2 — Early Confirmation",
            value=2
        ),
        app_commands.Choice(
            name="Tier 3 — Anticipatory / Risk Entry",
            value=3
        ),
    ]
)
async def trade(
    interaction: discord.Interaction,
    asset: str,
    direction: app_commands.Choice[str],
    play: app_commands.Choice[str],
    entry_model: app_commands.Choice[str],
    tier: app_commands.Choice[int],
    risk_r: float
):
    if not await require_member(interaction):
        return

    if risk_r <= 0:
        await interaction.response.send_message(
            "❌ Risk must be greater than 0R.",
            ephemeral=True
        )
        return

    if (
        play.value == "OTHER"
        or entry_model.value == "OTHER"
    ):
        await interaction.response.send_modal(
            TradeOtherDetailsModal(
                asset,
                direction.value,
                play.value,
                entry_model.value,
                tier.value,
                risk_r
            )
        )
        return

    await save_member_trade(
        interaction,
        asset,
        direction.value,
        play.value,
        entry_model.value,
        tier.value,
        risk_r
    )


# -----------------------------
# ADDITIONAL ENTRY FLOW
# -----------------------------

async def save_additional_entry(
    interaction,
    trade_id,
    entry_model,
    tier,
    risk_r
):
    trade_row = get_thesis(
        trade_id,
        interaction.user.id
    )

    if trade_row is None or trade_row["status"] != "OPEN":
        await interaction.response.send_message(
            "❌ That open trade could not be found.",
            ephemeral=True
        )
        return

    used_before = thesis_used_r(trade_id)
    projected_total = used_before + risk_r
    tier_limit = tier_max_r(tier)

    warnings = []

    if risk_r > tier_limit + 0.0001:
        warnings.append((
            "TIER_ALLOCATION",
            f"Tier {tier} guideline maximum is "
            f"{tier_limit:.2f}R, but {risk_r:.2f}R was used."
        ))

    if projected_total > 1.0 + 0.0001:
        warnings.append((
            "THESIS_BUDGET",
            f"This entry brings total recorded thesis risk to "
            f"{projected_total:.2f}R, above the 1.00R GTOP budget."
        ))

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO thesis_executions (
                thesis_id,
                guild_id,
                user_id,
                entry_model,
                tier,
                risk_r,
                entry_invalidation,
                note,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
        """, (
            trade_id,
            GTOP_GUILD_ID,
            interaction.user.id,
            entry_model,
            tier,
            risk_r,
            now()
        ))

        execution_id = cur.lastrowid

    for code_name, message in warnings:
        member_risk_flag(
            trade_id,
            execution_id,
            interaction.user.id,
            code_name,
            message
        )

    total = thesis_used_r(trade_id)
    remaining = max(0.0, 1.0 - total)

    warning_text = ""

    if warnings:
        warning_text = (
            "\n\n**⚠️ GTOP Risk Review**\n"
            + "\n".join(
                f"• {message}"
                for _, message in warnings
            )
            + "\n\nEntry was **saved and flagged**, not blocked."
        )

    visible_trade_number = trade_number_for_id(
        interaction.user.id,
        trade_id
    )

    await interaction.response.send_message(
        f"➕ **Additional entry saved to Trade #{visible_trade_number}.**\n"
        f"Asset: **{trade_row['asset']}**\n"
        f"Entry Model: **{entry_model}**\n"
        f"Tier: **{tier}**\n"
        f"Risk: **{risk_r:.2f}R**\n"
        f"Total Recorded Thesis Risk: **{total:.2f}R**\n"
        f"Remaining Protocol Budget: **{remaining:.2f}R**"
        f"{warning_text}",
        ephemeral=True
    )



class OtherAdditionalEntryModal(
    discord.ui.Modal,
    title="Custom Entry Model"
):
    custom_entry = discord.ui.TextInput(
        label="Entry Model Used",
        placeholder="Enter the actual entry model used...",
        required=True,
        max_length=150
    )

    def __init__(
        self,
        trade_id,
        tier,
        risk_r
    ):
        super().__init__()
        self.trade_id = trade_id
        self.tier_value = tier
        self.risk_value = risk_r

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        await save_additional_entry(
            interaction,
            self.trade_id,
            str(self.custom_entry).strip(),
            self.tier_value,
            self.risk_value
        )


@tree.command(
    name="entry",
    description="Add another entry to an existing GTOP trade.",
    guild=GUILD
)
@app_commands.describe(
    entry_model="Entry model used for this additional entry",
    tier="GTOP Risk Protocol tier",
    risk_r="Actual risk used on this entry",
    trade_id="Leave blank if you only have one open trade"
)
@app_commands.choices(
    entry_model=[
        app_commands.Choice(
            name="Model 1 / CSD",
            value="Model 1"
        ),
        app_commands.Choice(
            name="Turtle Wick Soup",
            value="Turtle Wick Soup"
        ),
        app_commands.Choice(
            name="Turtle Body Soup",
            value="Turtle Body Soup"
        ),
        app_commands.Choice(
            name="Super Soup",
            value="Super Soup"
        ),
        app_commands.Choice(
            name="Blessed Thief",
            value="Blessed Thief"
        ),
        app_commands.Choice(
            name="SMT Refinement",
            value="SMT"
        ),
        app_commands.Choice(
            name="88.7 / OTE Refinement",
            value="88.7 OTE"
        ),
        app_commands.Choice(
            name="Other",
            value="OTHER"
        ),
    ],
    tier=[
        app_commands.Choice(
            name="Tier 1 — Confirmed",
            value=1
        ),
        app_commands.Choice(
            name="Tier 2 — Early Confirmation",
            value=2
        ),
        app_commands.Choice(
            name="Tier 3 — Anticipatory / Risk Entry",
            value=3
        ),
    ]
)
async def entry(
    interaction: discord.Interaction,
    entry_model: app_commands.Choice[str],
    tier: app_commands.Choice[int],
    risk_r: float,
    trade_id: int = 0
):
    if not await require_member(interaction):
        return

    if risk_r <= 0:
        await interaction.response.send_message(
            "❌ Risk must be greater than 0R.",
            ephemeral=True
        )
        return

    selected_id = 0

    if trade_id:
        selected_id = trade_id_from_number(
            interaction.user.id,
            trade_id
        )

        if selected_id is None:
            await interaction.response.send_message(
                f"❌ Trade #{trade_id} could not be found.",
                ephemeral=True
            )
            return

    if selected_id:
        row = get_thesis(
            selected_id,
            interaction.user.id
        )

        if row is None or row["status"] != "OPEN":
            await interaction.response.send_message(
                "❌ That open trade could not be found.",
                ephemeral=True
            )
            return

    else:
        rows = open_member_trades(
            interaction.user.id
        )

        if not rows:
            await interaction.response.send_message(
                "You do not currently have an open trade.",
                ephemeral=True
            )
            return

        if len(rows) > 1:
            lines = [
                "**You have multiple open trades.**",
                "Run `/entry` again and enter the Trade #:",
            ]

            for row in rows[:10]:
                visible_number = trade_number_for_id(
                    interaction.user.id,
                    row["id"]
                )

                lines.append(
                    f"• `#{visible_number}` — "
                    f"{row['asset']} | "
                    f"{row['direction']} | "
                    f"{row['play']}"
                )

            await interaction.response.send_message(
                "\n".join(lines),
                ephemeral=True
            )
            return

        selected_id = rows[0]["id"]

    if entry_model.value == "OTHER":
        await interaction.response.send_modal(
            OtherAdditionalEntryModal(
                selected_id,
                tier.value,
                risk_r
            )
        )
        return

    await save_additional_entry(
        interaction,
        selected_id,
        entry_model.value,
        tier.value,
        risk_r
    )


class CloseMemberTradeModal(
    discord.ui.Modal,
    title="Close & Journal Trade"
):
    result_r = discord.ui.TextInput(
        label="Final Result in R",
        placeholder="Example: +3.0, -0.25, 0",
        required=False,
        max_length=20
    )

    rule_adherence = discord.ui.TextInput(
        label="Rule Adherence",
        placeholder="Followed / Partial deviation / Violated",
        required=True,
        max_length=100
    )

    summary = discord.ui.TextInput(
        label="What happened?",
        placeholder="Brief trade and management review...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    study_note = discord.ui.TextInput(
        label="Study Note",
        placeholder="What should you preserve or adjust next time?",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=700
    )

    def __init__(self, trade_id):
        super().__init__()
        self.trade_id = trade_id

    async def on_submit(
        self,
        interaction: discord.Interaction
    ):
        if not await require_member(interaction):
            return

        row = get_thesis(
            self.trade_id,
            interaction.user.id
        )

        if row is None:
            await interaction.response.send_message(
                "❌ Trade not found.",
                ephemeral=True
            )
            return

        raw_result = str(self.result_r).strip()
        result_value = None

        if raw_result:
            cleaned = (
                raw_result.upper()
                .replace("R", "")
                .strip()
            )

            try:
                result_value = float(cleaned)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Result must look like `+3`, `-0.25`, or `0`.",
                    ephemeral=True
                )
                return

        with db() as conn:
            conn.execute("""
                UPDATE theses
                SET status='CLOSED',
                    final_result_r=?,
                    close_note=?,
                    closed_at=?
                WHERE id=?
                  AND guild_id=?
                  AND user_id=?
            """, (
                result_value,
                str(self.summary).strip(),
                now(),
                self.trade_id,
                GTOP_GUILD_ID,
                interaction.user.id
            ))

            cur = conn.execute("""
                INSERT INTO journals (
                    guild_id,
                    user_id,
                    description,
                    rule_adherence,
                    result_r,
                    study_note,
                    created_at,
                    thesis_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                GTOP_GUILD_ID,
                interaction.user.id,
                str(self.summary).strip(),
                str(self.rule_adherence).strip(),
                result_value,
                str(self.study_note).strip(),
                now(),
                self.trade_id
            ))

            journal_id = cur.lastrowid

            flags = conn.execute("""
                SELECT COUNT(*)
                FROM risk_flags
                WHERE thesis_id=?
                  AND guild_id=?
                  AND user_id=?
            """, (
                self.trade_id,
                GTOP_GUILD_ID,
                interaction.user.id
            )).fetchone()[0]

        await interaction.response.send_message(
            f"✅ **Trade #{self.trade_id} closed and journaled.**\n"
            f"Asset: **{row['asset']}**\n"
            f"Play: **{row['play']}**\n"
            f"Final Result: **{format_r(result_value)}**\n"
            f"Risk Flags: **{flags}**\n"
            f"Journal: **#{journal_id}**",
            ephemeral=True
        )


@tree.command(
    name="close",
    description="Close your trade and complete the journal.",
    guild=GUILD
)
@app_commands.describe(
    trade_id="Leave blank if you only have one open trade."
)
async def close_member_trade(
    interaction: discord.Interaction,
    trade_id: int = 0
):
    if not await require_member(interaction):
        return

    selected_id = 0

    if trade_id:
        selected_id = trade_id_from_number(
            interaction.user.id,
            trade_id
        )

        if selected_id is None:
            await interaction.response.send_message(
                f"❌ Trade #{trade_id} could not be found.",
                ephemeral=True
            )
            return

    if selected_id:
        row = get_thesis(
            selected_id,
            interaction.user.id
        )

        if (
            row is None
            or row["status"] != "OPEN"
        ):
            await interaction.response.send_message(
                "❌ That open trade could not be found.",
                ephemeral=True
            )
            return

    else:
        rows = open_member_trades(
            interaction.user.id
        )

        if not rows:
            await interaction.response.send_message(
                "You do not currently have an open trade.",
                ephemeral=True
            )
            return

        if len(rows) > 1:
            lines = [
                "**You have multiple open trades.**",
                "Run `/close` again and choose the Trade #:",
            ]

            for row in rows[:10]:
                visible_number = trade_number_for_id(
                    interaction.user.id,
                    row["id"]
                )

                lines.append(
                    f"• `#{visible_number}` — "
                    f"{row['asset']} | "
                    f"{row['direction']} | "
                    f"{row['play']}"
                )

            await interaction.response.send_message(
                "\n".join(lines),
                ephemeral=True
            )
            return

        selected_id = rows[0]["id"]

    await interaction.response.send_modal(
        CloseMemberTradeModal(selected_id)
    )




# -----------------------------
# JOURNAL EDIT V1
# -----------------------------

def ensure_journal_edit_schema():
    with db() as conn:
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(journals)").fetchall()
        }
        if "thesis_id" not in cols:
            conn.execute(
                "ALTER TABLE journals ADD COLUMN thesis_id INTEGER"
            )


def get_member_journal(user_id: int, journal_id: int = 0):
    with db() as conn:
        if journal_id:
            return conn.execute(
                """
                SELECT *
                FROM journals
                WHERE id=?
                  AND guild_id=?
                  AND user_id=?
                """,
                (journal_id, GTOP_GUILD_ID, user_id),
            ).fetchone()

        return conn.execute(
            """
            SELECT *
            FROM journals
            WHERE guild_id=?
              AND user_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (GTOP_GUILD_ID, user_id),
        ).fetchone()


class EditJournalModal(discord.ui.Modal, title="Edit GBOP Journal"):
    def __init__(self, journal_row):
        super().__init__()
        self.journal_id = journal_row["id"]

        result_default = ""
        if journal_row["result_r"] is not None:
            result_default = str(journal_row["result_r"])

        self.summary = discord.ui.TextInput(
            label="What happened?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default=journal_row["description"] or "",
        )

        self.rule_adherence = discord.ui.TextInput(
            label="Rule Adherence",
            placeholder="Followed / Partial deviation / Violated",
            required=True,
            max_length=100,
            default=journal_row["rule_adherence"] or "",
        )

        self.result_r = discord.ui.TextInput(
            label="Final Result in R",
            placeholder="Example: +3.0, -0.25, 0",
            required=False,
            max_length=20,
            default=result_default,
        )

        self.study_note = discord.ui.TextInput(
            label="Study Note",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=700,
            default=journal_row["study_note"] or "",
        )

        self.add_item(self.summary)
        self.add_item(self.rule_adherence)
        self.add_item(self.result_r)
        self.add_item(self.study_note)

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_member(interaction):
            return

        row = get_member_journal(
            interaction.user.id,
            self.journal_id,
        )

        if row is None:
            await interaction.response.send_message(
                "❌ That journal entry could not be found.",
                ephemeral=True,
            )
            return

        raw_result = str(self.result_r).strip()
        result_value = None

        if raw_result:
            cleaned = raw_result.upper().replace("R", "").strip()
            try:
                result_value = float(cleaned)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Result must look like `+3`, `-0.25`, or `0`.",
                    ephemeral=True,
                )
                return

        with db() as conn:
            conn.execute(
                """
                UPDATE journals
                SET description=?,
                    rule_adherence=?,
                    result_r=?,
                    study_note=?
                WHERE id=?
                  AND guild_id=?
                  AND user_id=?
                """,
                (
                    str(self.summary).strip(),
                    str(self.rule_adherence).strip(),
                    result_value,
                    str(self.study_note).strip(),
                    self.journal_id,
                    GTOP_GUILD_ID,
                    interaction.user.id,
                ),
            )

            if row["thesis_id"]:
                conn.execute(
                    """
                    UPDATE theses
                    SET final_result_r=?,
                        close_note=?
                    WHERE id=?
                      AND guild_id=?
                      AND user_id=?
                    """,
                    (
                        result_value,
                        str(self.summary).strip(),
                        row["thesis_id"],
                        GTOP_GUILD_ID,
                        interaction.user.id,
                    ),
                )

        await interaction.response.send_message(
            f"✏️ **Journal #{self.journal_id} updated.**\n"
            f"Result: **{format_r(result_value)}**\n"
            f"Rule Adherence: **{self.rule_adherence}**",
            ephemeral=True,
        )


@tree.command(
    name="editjournal",
    description="Edit one of your previous GTOP journal entries.",
    guild=GUILD,
)
@app_commands.describe(
    journal_id="Leave blank to edit your most recent journal."
)
async def editjournal(
    interaction: discord.Interaction,
    journal_id: int = 0,
):
    if not await require_member(interaction):
        return

    row = get_member_journal(
        interaction.user.id,
        journal_id,
    )

    if row is None:
        await interaction.response.send_message(
            "❌ No matching journal entry was found.",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(
        EditJournalModal(row)
    )



# -----------------------------
# JOURNAL DELETE V1
# -----------------------------

def find_owned_journal(user_id: int, journal_id: int):
    with db() as conn:
        return conn.execute("""
            SELECT *
            FROM journals
            WHERE id=?
              AND guild_id=?
              AND user_id=?
        """, (
            journal_id,
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()


def delete_owned_journal(user_id: int, journal_id: int):
    row = find_owned_journal(user_id, journal_id)

    if row is None:
        return {"ok": False, "error": "No matching journal entry was found."}

    with db() as conn:
        conn.execute("""
            DELETE FROM journals
            WHERE id=?
              AND guild_id=?
              AND user_id=?
        """, (
            journal_id,
            GTOP_GUILD_ID,
            user_id,
        ))

    return {
        "ok": True,
        "journal_id": journal_id,
        "trade_id": row["thesis_id"],
        "result_r": row["result_r"],
        "rule_adherence": row["rule_adherence"],
        "summary": row["description"],
        "study_note": row["study_note"],
        "trade_preserved": True,
    }


class DeleteJournalView(discord.ui.View):
    def __init__(self, owner_id: int, journal_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.journal_id = journal_id

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the owner of this journal can confirm its deletion.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Delete Journal",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        result = delete_owned_journal(interaction.user.id, self.journal_id)

        if not result["ok"]:
            await interaction.response.edit_message(
                content="❌ That journal entry could not be found.",
                view=None,
            )
            self.stop()
            return

        trade_note = ""
        if result["trade_id"]:
            trade_note = (
                f"\nLinked Trade #{result['trade_id']} was **not** deleted."
            )

        await interaction.response.edit_message(
            content=(
                f"🗑️ **Journal #{self.journal_id} deleted.**"
                f"{trade_note}\n"
                "The underlying trade/execution history remains intact."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.edit_message(
            content=(
                f"Deletion cancelled. Journal #{self.journal_id} "
                "was not changed."
            ),
            view=None,
        )
        self.stop()


@tree.command(
    name="deletejournal",
    description="Delete one of your own GTOP journal entries.",
    guild=GUILD,
)
@app_commands.describe(
    journal_id="Journal ID to delete"
)
async def deletejournal(
    interaction: discord.Interaction,
    journal_id: int,
):
    if not await require_member(interaction):
        return

    row = find_owned_journal(interaction.user.id, journal_id)

    if row is None:
        await interaction.response.send_message(
            "❌ No matching journal entry was found.",
            ephemeral=True,
        )
        return

    result_text = (
        "Not specified"
        if row["result_r"] is None
        else f"{float(row['result_r']):+.2f}R"
    )

    trade_text = (
        "None"
        if not row["thesis_id"]
        else f"Trade #{row['thesis_id']}"
    )

    await interaction.response.send_message(
        (
            f"**Delete Journal #{journal_id}?**\n"
            f"Result: **{result_text}**\n"
            f"Rule Adherence: **{row['rule_adherence'] or 'Not specified'}**\n"
            f"Linked Trade: **{trade_text}**\n\n"
            "This deletes the journal entry only. "
            "The underlying trade and execution history will remain."
        ),
        view=DeleteJournalView(interaction.user.id, journal_id),
        ephemeral=True,
    )


# -----------------------------
# FULL TRADE DELETE V1
# -----------------------------

def find_owned_trade(user_id: int, trade_id: int):
    with db() as conn:
        return conn.execute("""
            SELECT *
            FROM theses
            WHERE id=?
              AND guild_id=?
              AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()


def get_trade_delete_preview(user_id: int, trade_id: int):
    row = find_owned_trade(user_id, trade_id)
    if row is None:
        return None

    with db() as conn:
        execution_count = conn.execute("""
            SELECT COUNT(*)
            FROM thesis_executions
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()[0]

        event_count = conn.execute("""
            SELECT COUNT(*)
            FROM thesis_events
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()[0]

        journal_count = conn.execute("""
            SELECT COUNT(*)
            FROM journals
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()[0]

        flag_count = conn.execute("""
            SELECT COUNT(*)
            FROM risk_flags
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()[0]

    return {
        "trade_id": trade_id,
        "asset": row["asset"],
        "direction": row["direction"],
        "play": row["play"],
        "status": row["status"],
        "result_r": row["final_result_r"],
        "executions": execution_count,
        "events": event_count,
        "journals": journal_count,
        "risk_flags": flag_count,
    }


def permanently_delete_trade(user_id: int, trade_id: int):
    visible_trade_number = trade_id

    trade_id = trade_id_from_number(
        user_id,
        visible_trade_number
    )

    if trade_id is None:
        return {
            "ok": False,
            "error": f"Trade #{visible_trade_number} was not found."
        }
    preview = get_trade_delete_preview(user_id, trade_id)    
    if preview is None:
        return {
            "ok": False,
            "error": "No matching trade was found.",
        }

    with db() as conn:
        conn.execute("BEGIN")
        conn.execute("""
            DELETE FROM journals
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        ))
        conn.execute("""
            DELETE FROM risk_flags
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        ))
        conn.execute("""
            DELETE FROM thesis_events
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        ))
        conn.execute("""
            DELETE FROM thesis_executions
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        ))
        conn.execute("""
            DELETE FROM theses
            WHERE id=? AND guild_id=? AND user_id=?
        """, (
            trade_id,
            GTOP_GUILD_ID,
            user_id,
        ))

    return {
        "ok": True,
        "deleted": True,
        **preview,
    }


class DeleteTradeView(discord.ui.View):
    def __init__(self, owner_id: int, trade_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.trade_id = trade_id

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the owner of this trade can confirm its deletion.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Delete Entire Trade",
        style=discord.ButtonStyle.danger,
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        result = permanently_delete_trade(
            interaction.user.id,
            self.trade_id,
        )

        if not result["ok"]:
            await interaction.response.edit_message(
                content="❌ That trade could not be found.",
                view=None,
            )
            self.stop()
            return

        await interaction.response.edit_message(
            content=(
                f"🗑️ **Trade #{self.trade_id} permanently deleted.**\n"
                f"Removed: **{result['executions']} execution(s)**, "
                f"**{result['events']} event(s)**, "
                f"**{result['journals']} linked journal(s)**, and "
                f"**{result['risk_flags']} risk flag(s)**."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.edit_message(
            content=(
                f"Deletion cancelled. Trade #{self.trade_id} "
                "and all linked records were left unchanged."
            ),
            view=None,
        )
        self.stop()


@tree.command(
    name="deletetrade",
    description="Permanently delete one of your trades and all linked records.",
    guild=GUILD,
)
@app_commands.describe(
    trade_id="Trade ID to permanently delete"
)
async def deletetrade(
    interaction: discord.Interaction,
    trade_id: int,
):
    if not await require_member(interaction):
        return

    visible_trade_number = trade_id

    internal_trade_id = trade_id_from_number(
        interaction.user.id,
        visible_trade_number
    )

    if internal_trade_id is None:
        await interaction.response.send_message(
            f"❌ Trade #{visible_trade_number} could not be found.",
            ephemeral=True
        )
        return

    preview = get_trade_delete_preview(
        interaction.user.id,
        internal_trade_id
    )

    if preview is None:
        await interaction.response.send_message(
            "❌ No matching trade was found.",
            ephemeral=True,
        )
        return

    result_text = (
        "Not specified"
        if preview["result_r"] is None
        else f"{float(preview['result_r']):+.2f}R"
    )

    await interaction.response.send_message(
        (
            f"**Permanently delete Trade #{trade_id}?**\n"
            f"Asset: **{preview['asset']}**\n"
            f"Direction: **{preview['direction']}**\n"
            f"Play: **{preview['play']}**\n"
            f"Status: **{preview['status']}**\n"
            f"Result: **{result_text}**\n\n"
            f"This will remove **{preview['executions']} execution(s)**, "
            f"**{preview['events']} event(s)**, "
            f"**{preview['journals']} linked journal(s)**, and "
            f"**{preview['risk_flags']} risk flag(s)**.\n\n"
            "**This cannot be undone.**"
        ),
        view=DeleteTradeView(
            interaction.user.id,
            trade_id,
        ),
        ephemeral=True,
    )


# -----------------------------
# BASIC COMMANDS
# -----------------------------

@tree.command(
    name="ping",
    description="Check whether GBOP is online.",
    guild=GUILD,
)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🏆 **GBOP is online.**\nGreatest Bot on the Planet.",
        ephemeral=True
    )


@tree.command(
    name="access",
    description="Check your current GBOP access.",
    guild=GUILD,
)
async def access(interaction: discord.Interaction):
    member = interaction.user

    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "Use this command inside G.T.O.P.",
            ephemeral=True
        )
        return

    ensure_member_record(member)
    record = get_member_record(member.id)

    if is_owner(member):
        level = "👑 GTOP Owner / Super Admin"
    elif is_admin(member):
        level = "🛡️ GTOP Admin"
    elif has_member_role(member):
        level = "✅ GTOP Member"
    else:
        level = "❌ No GBOP member access"

    await interaction.response.send_message(
        f"**GBOP Access Check**\n"
        f"User: {member.mention}\n"
        f"Access: {level}\n"
        f"GTOP Member Role: {'Yes ✅' if has_member_role(member) else 'No ❌'}\n"
        f"Profile Activated: {'Yes ✅' if record['activated'] else 'No ❌'}\n"
        f"Revoked: {'Yes ⛔' if record['revoked'] else 'No ✅'}",
        ephemeral=True
    )


# -----------------------------
# MEMBER ACTIVATION / PROFILE
# -----------------------------

@tree.command(
    name="activate",
    description="Activate your private GBOP member profile.",
    guild=GUILD,
)
@app_commands.describe(
    agree="Acknowledge that authorized GTOP leadership can review your journal for coaching/accountability."
)
async def activate(
    interaction: discord.Interaction,
    agree: bool
):
    member = interaction.user

    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "Use this command inside G.T.O.P.",
            ephemeral=True
        )
        return

    ensure_member_record(member)

    if not is_owner(member) and not has_member_role(member):
        await interaction.response.send_message(
            "❌ You need the GTOP member role before activating GBOP.",
            ephemeral=True
        )
        return

    record = get_member_record(member.id)

    if record["revoked"] and not is_owner(member):
        await interaction.response.send_message(
            "⛔ Your GBOP access is currently revoked.",
            ephemeral=True
        )
        return

    if not agree:
        await interaction.response.send_message(
            "**GBOP Privacy Notice**\n"
            "Your profile and trading journal are private from other GTOP members.\n"
            "Authorized GTOP leadership can review member journals for coaching and accountability.\n\n"
            "If you agree, run `/activate agree:true`.",
            ephemeral=True
        )
        return

    with db() as conn:
        conn.execute("""
            UPDATE members
            SET activated=1,
                leadership_ack=1,
                updated_at=?
            WHERE guild_id=? AND user_id=?
        """, (now(), GTOP_GUILD_ID, member.id))

    await interaction.response.send_message(
        "✅ **GBOP profile activated.**\n"
        "Your member profile is now ready.\n\n"
        "Your future journal and thesis records will be isolated from other members. "
        "Authorized GTOP leadership retains review access for coaching/accountability.",
        ephemeral=True
    )


@tree.command(
    name="profile",
    description="View your private GBOP profile.",
    guild=GUILD,
)
async def profile(interaction: discord.Interaction):
    if not await require_member(interaction):
        return

    member = interaction.user
    record = get_member_record(member.id)

    if is_owner(member):
        level = "GTOP Owner / Super Admin"
    elif is_admin(member):
        level = "GTOP Admin"
    else:
        level = "GTOP Member"

    await interaction.response.send_message(
        f"**GBOP Profile**\n"
        f"Member: {member.mention}\n"
        f"Access Level: {level}\n"
        f"Discord ID: `{member.id}`\n"
        f"Profile Activated: {'✅' if record['activated'] else '❌'}\n"
        f"Leadership Review Acknowledged: {'✅' if record['leadership_ack'] else '❌'}\n"
        f"Access Revoked: {'⛔ Yes' if record['revoked'] else '✅ No'}",
        ephemeral=True
    )


# -----------------------------
# ADMIN COMMAND GROUP
# -----------------------------

admin = app_commands.Group(
    name="admin",
    description="GTOP leadership controls."
)


@admin.command(
    name="member",
    description="View a GTOP member's GBOP profile."
)
async def admin_member(
    interaction: discord.Interaction,
    member: discord.Member
):
    if not await require_admin(interaction):
        return

    ensure_member_record(member)
    record = get_member_record(member.id)

    await interaction.response.send_message(
        f"**GBOP Admin — Member Profile**\n"
        f"Member: {member.mention}\n"
        f"Discord ID: `{member.id}`\n"
        f"Has GTOP Role: {'✅' if has_member_role(member) else '❌'}\n"
        f"Activated: {'✅' if record['activated'] else '❌'}\n"
        f"Leadership Ack: {'✅' if record['leadership_ack'] else '❌'}\n"
        f"Revoked: {'⛔ Yes' if record['revoked'] else '✅ No'}",
        ephemeral=True
    )


@admin.command(
    name="revoke",
    description="Disable a member's GBOP access without deleting their records."
)
async def admin_revoke(
    interaction: discord.Interaction,
    member: discord.Member
):
    if not await require_admin(interaction):
        return

    if member.id == GTOP_OWNER_USER_ID:
        await interaction.response.send_message(
            "❌ The GTOP Owner account cannot be revoked.",
            ephemeral=True
        )
        return

    ensure_member_record(member)

    with db() as conn:
        conn.execute("""
            UPDATE members
            SET revoked=1, updated_at=?
            WHERE guild_id=? AND user_id=?
        """, (now(), GTOP_GUILD_ID, member.id))

    await interaction.response.send_message(
        f"⛔ **GBOP access revoked for {member.mention}.**\n"
        "Their stored profile and future journal history remain intact.",
        ephemeral=True
    )


@admin.command(
    name="restore",
    description="Restore a member's GBOP access."
)
async def admin_restore(
    interaction: discord.Interaction,
    member: discord.Member
):
    if not await require_admin(interaction):
        return

    ensure_member_record(member)

    with db() as conn:
        conn.execute("""
            UPDATE members
            SET revoked=0, updated_at=?
            WHERE guild_id=? AND user_id=?
        """, (now(), GTOP_GUILD_ID, member.id))

    await interaction.response.send_message(
        f"✅ **GBOP access restored for {member.mention}.**",
        ephemeral=True
    )


@admin.command(
    name="members",
    description="Show GBOP member profile counts."
)
async def admin_members(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM members WHERE guild_id=?",
            (GTOP_GUILD_ID,)
        ).fetchone()[0]

        activated = conn.execute(
            "SELECT COUNT(*) FROM members WHERE guild_id=? AND activated=1",
            (GTOP_GUILD_ID,)
        ).fetchone()[0]

        revoked = conn.execute(
            "SELECT COUNT(*) FROM members WHERE guild_id=? AND revoked=1",
            (GTOP_GUILD_ID,)
        ).fetchone()[0]

    await interaction.response.send_message(
        f"**GBOP Member Overview**\n"
        f"Stored Profiles: **{total}**\n"
        f"Activated: **{activated}**\n"
        f"Revoked: **{revoked}**",
        ephemeral=True
    )




@admin.command(
    name="journal",
    description="View a member's recent private GTOP journal entries."
)
@app_commands.describe(
    member="GTOP member whose journal you want to review",
    limit="Number of entries to show, 1-10"
)
async def admin_journal(
    interaction: discord.Interaction,
    member: discord.Member,
    limit: int = 5
):
    if not await require_admin(interaction):
        return

    limit = max(1, min(limit, 10))

    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM journals
            WHERE guild_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            GTOP_GUILD_ID,
            member.id,
            limit
        )).fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No journal entries found for {member.mention}.",
            ephemeral=True
        )
        return

    parts = [
        f"**GBOP Admin — {member.display_name}'s Recent Journal**"
    ]

    for row in rows:
        parts.append(
            f"\n**Journal #{row['id']}**"
            f"\nResult: {format_r(row['result_r'])}"
            f"\nAdherence: {row['rule_adherence'] or 'Not specified'}"
            f"\nEntry: {row['description'][:450]}"
            f"\nStudy Note: {row['study_note'] or 'Not specified'}"
        )

    await interaction.response.send_message(
        "\n".join(parts)[:1900],
        ephemeral=True
    )




@admin.command(
    name="theses",
    description="View a member's recent GTOP theses."
)
async def admin_theses(
    interaction: discord.Interaction,
    member: discord.Member,
    limit: int = 5
):
    if not await require_admin(interaction):
        return

    limit = max(1, min(limit, 10))

    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM theses
            WHERE guild_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            GTOP_GUILD_ID,
            member.id,
            limit
        )).fetchall()

    if not rows:
        await interaction.response.send_message(
            f"No theses found for {member.mention}.",
            ephemeral=True
        )
        return

    parts = [
        f"**GBOP Admin — {member.display_name}'s Recent Theses**"
    ]

    for row in rows:
        used = thesis_used_r(row["id"])
        remaining = thesis_remaining_r(row["id"])

        parts.append(
            f"\n**Thesis #{row['id']} — {row['asset']}**"
            f"\n{row['direction']} | {row['play']} | {row['status']}"
            f"\nRisk Used: {used:.2f}R"
            f"\nRemaining: {remaining:.2f}R"
            f"\nObjective: {row['objective'][:200]}"
        )

    await interaction.response.send_message(
        "\n".join(parts)[:1900],
        ephemeral=True
    )


tree.add_command(admin, guild=GUILD)


# -----------------------------
# STARTUP
# -----------------------------

@client.event
async def setup_hook():
    print("[GBOP-STARTUP] Discord login succeeded; initializing database.")
    # Run each database step in order without blocking Discord's event loop.
    for initializer in (
        init_db,
        init_journal_db,
        init_thesis_db,
        init_risk_flags_db,
        init_member_trade_flow_db,
        ensure_journal_edit_schema,
    ):
        print(f"[GBOP-STARTUP] Starting {initializer.__name__}")
        try:
            await asyncio.to_thread(initializer)
        except Exception:
            logger.exception("Database startup failed at %s", initializer.__name__)
            raise
        print(f"[GBOP-STARTUP] Finished {initializer.__name__}")

    # Keep internal database-style commands hidden from normal members.
    tree.remove_command("thesis", guild=GUILD)
    tree.remove_command("execution", guild=GUILD)

    print("[GBOP-STARTUP] Syncing Discord commands.")
    synced = await tree.sync(guild=GUILD)
    print(f"[GBOP-STARTUP] Synced {len(synced)} top-level command(s) to G.T.O.P.")


@client.event
async def on_ready():
    print("=" * 55)
    print("GBOP ONLINE ✅")
    print(f"Logged in as: {client.user}")
    print(f"Bot User ID: {client.user.id}")
    print(f"GTOP Guild ID: {GTOP_GUILD_ID}")
    print(f"Database: {DB_PATH}")
    print("=" * 55)


# -----------------------------
# CONVERSATIONAL GBOP AI V1
# -----------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing from .env")

ai_client = OpenAI(api_key=OPENAI_API_KEY)

GTOP_AI_PROMPT = """
You are GBOP — Greatest Bot on the Planet — the conversational GTOP AI for
Greatest Traders On Planet.

You are an interactive trading assistant, coach, journal assistant, and GTOP
knowledge guide. Speak naturally and directly. Members should not need to
think in slash commands.

CORE BEHAVIOR
- Understand ordinary language such as:
  "I'm buying NAS off a 5 minute Turtle Wick Soup risking .25R."
  "Added another .25 on Model 1."
  "I'm out +2.8R. Followed plan but should have protected near 88.7."
- Ask only for information that is genuinely missing before taking an action.
- Never invent an asset, direction, play, entry model, risk, result, objective,
  invalidation, purge, closure, timeframe, or confirmation.
- When enough information is present, use the available tools to update GBOP's
  database. Never claim something was saved unless a tool confirms success.
- If there is exactly one open trade, natural phrases such as "added another
  .25R" may refer to that trade. If there are multiple plausible open trades,
  ask which one.
- Custom Plays and Entry Models are allowed. GTOP canonical terminology should
  be preserved when it applies, but never force a member's real trade into a
  playbook label that does not fit.
- Risk violations are WARN + SAVE. Do not erase or refuse to record an actual
  trade merely because the member broke protocol.
- Separate entry failure from thesis failure.
- One directional thesis has one 1R protocol budget across executions.
- Tier 1: confirmed execution, up to 1.00R.
- Tier 2: early confirmation, up to 0.50R.
- Tier 3: anticipatory/risk entry, up to 0.33R.
- A stopped execution does not reset the thesis budget.
- When total recorded risk exceeds 1R, flag it but preserve the record.
- When price has delivered roughly 80%-88.7% of the predetermined objective,
  recognize GTOP Profit Protection Mode. The usual protection concept is
  securing approximately +0.20R to +0.30R while respecting structure.
- 9ate8 is written exactly "9ate8".
- Model 1 / CSD is an execution-confirmation mechanism, not a standalone play.
- Turtle Wick Soup is lower confirmation and is commonly managed toward about
  50% of the range.
- A closure outside the selected 9ate8 range is the key range invalidation.
- Never treat mere stalling as structural invalidation.
- Do not hype trades or use certainty language.

CONVERSATIONAL WORKFLOW
- New trade idea: gather the minimum needed details, then call open_trade.
- Additional entry: call add_entry.
- Mid-trade development: call record_trade_event.
- Closing/reflection: gather final result when known, rule adherence, a concise
  summary, and a study note; then call close_trade.
- History/review questions: use the current member context and, when useful,
  get_journal_history or get_trade_state.
- Journal corrections: use edit_journal.
- Cleanup rule: if a member says a trade was a test, duplicate, accident, or asks to erase/remove a trade completely, treat full trade deletion as the primary cleanup action. First identify the exact trade and preview what will be removed. Only call delete_trade with confirm=true after explicit confirmation. Full deletion removes the trade idea, every execution, events, risk flags, and linked journals.
- Journal deletion: never permanently delete a journal without explicit confirmation from the member. First identify/preview the exact journal, then only call delete_journal with confirm=true after they clearly confirm deletion. Deleting a journal does not delete the underlying trade/execution history.
- If the user is simply asking a GTOP question, answer it conversationally
  without forcing a database action.

This is decision-support and education. Do not present uncertain market
interpretations as facts.
""".strip()


def init_ai_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis_id INTEGER NOT NULL,
                execution_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rule_code TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cols = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(journals)"
            ).fetchall()
        }
        if "thesis_id" not in cols:
            conn.execute(
                "ALTER TABLE journals ADD COLUMN thesis_id INTEGER"
            )


def ai_save_message(user_id: int, role: str, content: str):
    content = (content or "").strip()
    if not content:
        return

    with db() as conn:
        conn.execute("""
            INSERT INTO ai_messages (
                guild_id, user_id, role, content, created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            GTOP_GUILD_ID,
            user_id,
            role,
            content[:6000],
            now(),
        ))

        conn.execute("""
            DELETE FROM ai_messages
            WHERE guild_id=?
              AND user_id=?
              AND id NOT IN (
                  SELECT id
                  FROM ai_messages
                  WHERE guild_id=?
                    AND user_id=?
                  ORDER BY id DESC
                  LIMIT 20
              )
        """, (
            GTOP_GUILD_ID,
            user_id,
            GTOP_GUILD_ID,
            user_id,
        ))


def ai_recent_messages(user_id: int, limit: int = 10):
    with db() as conn:
        rows = conn.execute("""
            SELECT role, content
            FROM ai_messages
            WHERE guild_id=?
              AND user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            GTOP_GUILD_ID,
            user_id,
            max(1, min(limit, 20)),
        )).fetchall()

    rows = list(reversed(rows))
    return [
        {"role": row["role"], "content": row["content"]}
        for row in rows
    ]


async def ai_resolve_member(user_id: int):
    guild = client.get_guild(GTOP_GUILD_ID)
    if guild is None:
        return None

    member = guild.get_member(user_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


def ai_member_context(user_id: int):
    init_ai_db()

    with db() as conn:
        open_trades = conn.execute("""
            SELECT *
            FROM theses
            WHERE guild_id=?
              AND user_id=?
              AND status='OPEN'
            ORDER BY id DESC
            LIMIT 5
        """, (
            GTOP_GUILD_ID,
            user_id,
        )).fetchall()

        journals = conn.execute("""
            SELECT *
            FROM journals
            WHERE guild_id=?
              AND user_id=?
            ORDER BY id DESC
            LIMIT 5
        """, (
            GTOP_GUILD_ID,
            user_id,
        )).fetchall()

    lines = ["CURRENT MEMBER STATE"]

    if open_trades:
        lines.append("Open trades:")
        for row in open_trades:
            used = thesis_used_r(row["id"])
            lines.append(
                f"- Trade #{row['id']}: {row['asset']} | "
                f"{row['direction']} | Play: {row['play']} | "
                f"Recorded risk: {used:.2f}R | "
                f"Objective: {row['objective']}"
            )
    else:
        lines.append("Open trades: none.")

    if journals:
        lines.append("Recent journals:")
        for row in journals:
            result = (
                "not specified"
                if row["result_r"] is None
                else f"{float(row['result_r']):+.2f}R"
            )
            lines.append(
                f"- Journal #{row['id']}: result {result}; "
                f"adherence {row['rule_adherence'] or 'not specified'}; "
                f"note {row['study_note'] or 'not specified'}"
            )
    else:
        lines.append("Recent journals: none.")

    return "\n".join(lines)


def ai_infer_tier(entry_model: str, supplied_tier):
    if supplied_tier in (1, 2, 3):
        return supplied_tier

    name = (entry_model or "").strip().lower()

    if "model 1" in name or "csd" in name:
        return 1
    if "turtle wick" in name or "wick soup" in name:
        return 2
    if "super soup" in name:
        return 3
    if "blessed thief" in name:
        return 3
    if "88.7" in name or "ote" in name:
        return 3

    return None


def ai_flag_risk(thesis_id, execution_id, user_id, rule_code, message):
    with db() as conn:
        conn.execute("""
            INSERT INTO risk_flags (
                thesis_id,
                execution_id,
                guild_id,
                user_id,
                rule_code,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            thesis_id,
            execution_id,
            GTOP_GUILD_ID,
            user_id,
            rule_code,
            message,
            now(),
        ))


def ai_open_trade(user_id: int, args: dict):
    asset = str(args["asset"]).strip()
    direction = str(args["direction"]).strip()
    play = str(args["play"]).strip()
    entry_model = str(args["entry_model"]).strip()
    risk_r = float(args["risk_r"])
    tier = ai_infer_tier(entry_model, args.get("tier"))
    objective = (args.get("objective") or "Not specified at entry").strip()
    invalidation = (
        args.get("thesis_invalidation")
        or "Not specified at entry"
    ).strip()

    if risk_r <= 0:
        return {"ok": False, "error": "Risk must be greater than 0R."}

    if tier is None:
        return {
            "ok": False,
            "needs": "tier",
            "message": (
                "The entry model is custom or ambiguous, so the GTOP risk tier "
                "cannot be inferred reliably. Ask the member which tier applies."
            ),
        }

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO theses (
                guild_id, user_id, asset, direction, play, session,
                crt_variant, htf_context, liquidity_purged, objective,
                thesis_invalidation, status, max_r, created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, '', '', '', '', ?, ?, 'OPEN', 1.0, ?
            )
        """, (
            GTOP_GUILD_ID,
            user_id,
            asset,
            direction,
            play,
            objective,
            invalidation,
            now(),
        ))
        thesis_id = cur.lastrowid

        cur = conn.execute("""
            INSERT INTO thesis_executions (
                thesis_id, guild_id, user_id, entry_model, tier, risk_r,
                entry_invalidation, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
        """, (
            thesis_id,
            GTOP_GUILD_ID,
            user_id,
            entry_model,
            tier,
            risk_r,
            now(),
        ))
        execution_id = cur.lastrowid

    warnings = []
    tier_limit = tier_max_r(tier)

    if risk_r > tier_limit + 0.0001:
        msg = (
            f"Tier {tier} guideline maximum is {tier_limit:.2f}R, "
            f"but {risk_r:.2f}R was recorded."
        )
        warnings.append(msg)
        ai_flag_risk(
            thesis_id, execution_id, user_id, "TIER_ALLOCATION", msg
        )

    if risk_r > 1.0 + 0.0001:
        msg = (
            f"The 1.00R thesis budget was exceeded; "
            f"{risk_r:.2f}R was recorded."
        )
        warnings.append(msg)
        ai_flag_risk(
            thesis_id, execution_id, user_id, "THESIS_BUDGET", msg
        )

    return {
        "ok": True,
        "trade_id": thesis_id,
        "execution_id": execution_id,
        "asset": asset,
        "direction": direction,
        "play": play,
        "entry_model": entry_model,
        "tier": tier,
        "risk_r": risk_r,
        "warnings": warnings,
    }


def ai_choose_open_trade(user_id: int, trade_id):
    with db() as conn:
        if trade_id is not None:
            row = conn.execute("""
                SELECT *
                FROM theses
                WHERE id=? AND guild_id=? AND user_id=? AND status='OPEN'
            """, (
                int(trade_id),
                GTOP_GUILD_ID,
                user_id,
            )).fetchone()

            if row is None:
                return None, "That open trade could not be found."
            return row, None

        rows = conn.execute("""
            SELECT *
            FROM theses
            WHERE guild_id=? AND user_id=? AND status='OPEN'
            ORDER BY id DESC
        """, (
            GTOP_GUILD_ID,
            user_id,
        )).fetchall()

    if not rows:
        return None, "The member has no open trades."

    if len(rows) > 1:
        return None, {
            "needs_trade_selection": True,
            "open_trades": [
                {
                    "trade_id": row["id"],
                    "asset": row["asset"],
                    "direction": row["direction"],
                    "play": row["play"],
                }
                for row in rows[:10]
            ],
        }

    return rows[0], None


def ai_add_entry(user_id: int, args: dict):
    row, err = ai_choose_open_trade(user_id, args.get("trade_id"))
    if err:
        return {"ok": False, "error": err}

    entry_model = str(args["entry_model"]).strip()
    risk_r = float(args["risk_r"])
    tier = ai_infer_tier(entry_model, args.get("tier"))

    if risk_r <= 0:
        return {"ok": False, "error": "Risk must be greater than 0R."}

    if tier is None:
        return {
            "ok": False,
            "needs": "tier",
            "message": (
                "The entry model is custom or ambiguous. Ask which GTOP risk "
                "tier applies before saving the entry."
            ),
        }

    used_before = thesis_used_r(row["id"])
    projected = used_before + risk_r

    with db() as conn:
        cur = conn.execute("""
            INSERT INTO thesis_executions (
                thesis_id, guild_id, user_id, entry_model, tier, risk_r,
                entry_invalidation, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
        """, (
            row["id"],
            GTOP_GUILD_ID,
            user_id,
            entry_model,
            tier,
            risk_r,
            now(),
        ))
        execution_id = cur.lastrowid

    warnings = []
    tier_limit = tier_max_r(tier)

    if risk_r > tier_limit + 0.0001:
        msg = (
            f"Tier {tier} guideline maximum is {tier_limit:.2f}R, "
            f"but {risk_r:.2f}R was recorded."
        )
        warnings.append(msg)
        ai_flag_risk(
            row["id"], execution_id, user_id, "TIER_ALLOCATION", msg
        )

    if projected > 1.0 + 0.0001:
        msg = (
            f"Recorded thesis risk is now {projected:.2f}R, "
            f"above the 1.00R protocol budget."
        )
        warnings.append(msg)
        ai_flag_risk(
            row["id"], execution_id, user_id, "THESIS_BUDGET", msg
        )

    return {
        "ok": True,
        "trade_id": row["id"],
        "execution_id": execution_id,
        "entry_model": entry_model,
        "tier": tier,
        "risk_r": risk_r,
        "total_recorded_risk": thesis_used_r(row["id"]),
        "warnings": warnings,
    }


def ai_record_trade_event(user_id: int, args: dict):
    row, err = ai_choose_open_trade(user_id, args.get("trade_id"))
    if err:
        return {"ok": False, "error": err}

    event = str(args["event"]).strip()
    details = (args.get("details") or "").strip()
    result_r = args.get("result_r")
    if result_r is not None:
        result_r = float(result_r)

    with db() as conn:
        conn.execute("""
            INSERT INTO thesis_events (
                thesis_id, guild_id, user_id, event, details, result_r,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            row["id"],
            GTOP_GUILD_ID,
            user_id,
            event,
            details,
            result_r,
            now(),
        ))

    return {
        "ok": True,
        "trade_id": row["id"],
        "event": event,
        "details": details,
        "result_r": result_r,
        "total_recorded_risk": thesis_used_r(row["id"]),
    }


def ai_close_trade(user_id: int, args: dict):
    row, err = ai_choose_open_trade(user_id, args.get("trade_id"))
    if err:
        return {"ok": False, "error": err}

    final_result = args.get("final_result_r")
    if final_result is not None:
        final_result = float(final_result)

    adherence = str(args["rule_adherence"]).strip()
    summary = str(args["summary"]).strip()
    study_note = str(args["study_note"]).strip()

    with db() as conn:
        conn.execute("""
            UPDATE theses
            SET status='CLOSED',
                final_result_r=?,
                close_note=?,
                closed_at=?
            WHERE id=? AND guild_id=? AND user_id=?
        """, (
            final_result,
            summary,
            now(),
            row["id"],
            GTOP_GUILD_ID,
            user_id,
        ))

        cur = conn.execute("""
            INSERT INTO journals (
                guild_id, user_id, description, rule_adherence, result_r,
                study_note, created_at, thesis_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            GTOP_GUILD_ID,
            user_id,
            summary,
            adherence,
            final_result,
            study_note,
            now(),
            row["id"],
        ))
        journal_id = cur.lastrowid

        flags = conn.execute("""
            SELECT COUNT(*)
            FROM risk_flags
            WHERE thesis_id=? AND guild_id=? AND user_id=?
        """, (
            row["id"],
            GTOP_GUILD_ID,
            user_id,
        )).fetchone()[0]

    return {
        "ok": True,
        "trade_id": row["id"],
        "journal_id": journal_id,
        "asset": row["asset"],
        "play": row["play"],
        "final_result_r": final_result,
        "rule_adherence": adherence,
        "risk_flags": flags,
        "recorded_thesis_risk": thesis_used_r(row["id"]),
    }


def ai_get_trade_state(user_id: int, args: dict):
    trade_id = args.get("trade_id")

    with db() as conn:
        if trade_id is not None:
            rows = conn.execute("""
                SELECT *
                FROM theses
                WHERE id=? AND guild_id=? AND user_id=?
            """, (
                int(trade_id),
                GTOP_GUILD_ID,
                user_id,
            )).fetchall()
        else:
            rows = conn.execute("""
                SELECT *
                FROM theses
                WHERE guild_id=? AND user_id=? AND status='OPEN'
                ORDER BY id DESC
                LIMIT 10
            """, (
                GTOP_GUILD_ID,
                user_id,
            )).fetchall()

    return {
        "ok": True,
        "trades": [
            {
                "trade_id": row["id"],
                "asset": row["asset"],
                "direction": row["direction"],
                "play": row["play"],
                "status": row["status"],
                "objective": row["objective"],
                "thesis_invalidation": row["thesis_invalidation"],
                "recorded_risk_r": thesis_used_r(row["id"]),
            }
            for row in rows
        ],
    }


def ai_get_journal_history(user_id: int, args: dict):
    limit = max(1, min(int(args.get("limit") or 5), 10))

    with db() as conn:
        rows = conn.execute("""
            SELECT *
            FROM journals
            WHERE guild_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT ?
        """, (
            GTOP_GUILD_ID,
            user_id,
            limit,
        )).fetchall()

    return {
        "ok": True,
        "journals": [
            {
                "journal_id": row["id"],
                "trade_id": row["thesis_id"],
                "result_r": row["result_r"],
                "rule_adherence": row["rule_adherence"],
                "summary": row["description"],
                "study_note": row["study_note"],
            }
            for row in rows
        ],
    }


def ai_edit_journal(user_id: int, args: dict):
    journal_id = args.get("journal_id")

    with db() as conn:
        if journal_id is None:
            row = conn.execute("""
                SELECT *
                FROM journals
                WHERE guild_id=? AND user_id=?
                ORDER BY id DESC
                LIMIT 1
            """, (
                GTOP_GUILD_ID,
                user_id,
            )).fetchone()
        else:
            row = conn.execute("""
                SELECT *
                FROM journals
                WHERE id=? AND guild_id=? AND user_id=?
            """, (
                int(journal_id),
                GTOP_GUILD_ID,
                user_id,
            )).fetchone()

    if row is None:
        return {"ok": False, "error": "No matching journal was found."}

    summary = args.get("summary")
    adherence = args.get("rule_adherence")
    result_r = args.get("result_r")
    study_note = args.get("study_note")

    new_summary = row["description"] if summary is None else str(summary).strip()
    new_adherence = (
        row["rule_adherence"] if adherence is None else str(adherence).strip()
    )
    new_result = row["result_r"] if result_r is None else float(result_r)
    new_note = row["study_note"] if study_note is None else str(study_note).strip()

    with db() as conn:
        conn.execute("""
            UPDATE journals
            SET description=?, rule_adherence=?, result_r=?, study_note=?
            WHERE id=? AND guild_id=? AND user_id=?
        """, (
            new_summary,
            new_adherence,
            new_result,
            new_note,
            row["id"],
            GTOP_GUILD_ID,
            user_id,
        ))

        if row["thesis_id"]:
            conn.execute("""
                UPDATE theses
                SET final_result_r=?, close_note=?
                WHERE id=? AND guild_id=? AND user_id=?
            """, (
                new_result,
                new_summary,
                row["thesis_id"],
                GTOP_GUILD_ID,
                user_id,
            ))

    return {
        "ok": True,
        "journal_id": row["id"],
        "trade_id": row["thesis_id"],
        "result_r": new_result,
        "rule_adherence": new_adherence,
        "summary": new_summary,
        "study_note": new_note,
    }


GBOP_AI_TOOLS = [
    {
        "type": "function",
        "name": "open_trade",
        "description": "Create a new trade idea and its first execution.",
        "parameters": {
            "type": "object",
            "properties": {
                "asset": {"type": "string"},
                "direction": {
                    "type": "string",
                    "enum": ["Bullish", "Bearish"],
                },
                "play": {"type": "string"},
                "entry_model": {"type": "string"},
                "tier": {
                    "type": ["integer", "null"],
                    "enum": [1, 2, 3, None],
                },
                "risk_r": {"type": "number"},
                "objective": {"type": ["string", "null"]},
                "thesis_invalidation": {"type": ["string", "null"]},
            },
            "required": [
                "asset",
                "direction",
                "play",
                "entry_model",
                "tier",
                "risk_r",
                "objective",
                "thesis_invalidation",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "add_entry",
        "description": "Record another entry under an existing open trade idea.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"]},
                "entry_model": {"type": "string"},
                "tier": {
                    "type": ["integer", "null"],
                    "enum": [1, 2, 3, None],
                },
                "risk_r": {"type": "number"},
            },
            "required": ["trade_id", "entry_model", "tier", "risk_r"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "record_trade_event",
        "description": (
            "Record a mid-trade development such as a stop, partial, "
            "profit protection, target hit, or management update."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"]},
                "event": {"type": "string"},
                "details": {"type": ["string", "null"]},
                "result_r": {"type": ["number", "null"]},
            },
            "required": ["trade_id", "event", "details", "result_r"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "close_trade",
        "description": "Close an open trade and create the linked GTOP journal.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"]},
                "final_result_r": {"type": ["number", "null"]},
                "rule_adherence": {"type": "string"},
                "summary": {"type": "string"},
                "study_note": {"type": "string"},
            },
            "required": [
                "trade_id",
                "final_result_r",
                "rule_adherence",
                "summary",
                "study_note",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_trade_state",
        "description": "Read the member's open trade state or a specific trade.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"]},
            },
            "required": ["trade_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_journal_history",
        "description": "Read the member's own recent GTOP journals.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "edit_journal",
        "description": (
            "Edit the member's own previous journal. Null fields mean keep "
            "the current value. A null journal_id means the latest journal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "journal_id": {"type": ["integer", "null"]},
                "summary": {"type": ["string", "null"]},
                "rule_adherence": {"type": ["string", "null"]},
                "result_r": {"type": ["number", "null"]},
                "study_note": {"type": ["string", "null"]},
            },
            "required": [
                "journal_id",
                "summary",
                "rule_adherence",
                "result_r",
                "study_note",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
]



def ai_delete_journal(user_id: int, args: dict):
    journal_id = args.get("journal_id")
    confirm = bool(args.get("confirm"))

    if journal_id is None:
        return {"ok": False, "error": "A journal ID is required for deletion."}

    row = find_owned_journal(user_id, int(journal_id))
    if row is None:
        return {"ok": False, "error": "No matching journal entry was found."}

    preview = {
        "journal_id": row["id"],
        "trade_id": row["thesis_id"],
        "result_r": row["result_r"],
        "rule_adherence": row["rule_adherence"],
        "summary": row["description"],
        "study_note": row["study_note"],
    }

    if not confirm:
        return {
            "ok": True,
            "requires_confirmation": True,
            "preview": preview,
            "message": (
                "Do not delete yet. Ask the member to explicitly confirm "
                "that they want this journal permanently deleted. "
                "The linked trade/execution history will remain intact."
            ),
        }

    result = delete_owned_journal(user_id, int(journal_id))
    if not result["ok"]:
        return result

    return {
        "ok": True,
        "deleted": True,
        "journal_id": result["journal_id"],
        "trade_id": result["trade_id"],
        "trade_preserved": True,
    }



GBOP_AI_TOOLS.append(
    {
        "type": "function",
        "name": "delete_journal",
        "description": (
            "Preview or permanently delete one of the member's own journals. "
            "Never call with confirm=true until the member explicitly confirms "
            "deletion after seeing or clearly identifying the journal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "journal_id": {"type": "integer"},
                "confirm": {"type": "boolean"},
            },
            "required": ["journal_id", "confirm"],
            "additionalProperties": False,
        },
        "strict": True,
    }
)



def ai_delete_trade(user_id: int, args: dict):
    trade_id = args.get("trade_id")
    confirm = bool(args.get("confirm"))

    if trade_id is None:
        return {
            "ok": False,
            "error": "A trade ID is required for permanent deletion.",
        }

    visible_trade_number = int(trade_id)

    internal_trade_id = trade_id_from_number(
        user_id,
        visible_trade_number
    )

    if internal_trade_id is None:
        return {
            "ok": False,
            "error": f"Trade #{visible_trade_number} was not found."
        }

    preview = get_trade_delete_preview(
        user_id,
        internal_trade_id
    )

    if preview is None:
        return {
            "ok": False,
            "error": "No matching trade was found.",
        }

    if not confirm:
        return {
            "ok": True,
            "requires_confirmation": True,
            "preview": preview,
            "message": (
                "Do not delete yet. Explain exactly what will be removed and "
                "ask the member for explicit confirmation. This is permanent."
            ),
        }

    result = permanently_delete_trade(
        user_id,
        visible_trade_number,
    )

    return result



GBOP_AI_TOOLS.append(
    {
        "type": "function",
        "name": "delete_trade",
        "description": (
            "Preview or permanently delete one of the member's complete trade "
            "records, including executions, events, risk flags, and linked "
            "journals. Never use confirm=true until the member explicitly "
            "confirms permanent deletion of that exact trade."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": "integer"},
                "confirm": {"type": "boolean"},
            },
            "required": [
                "trade_id",
                "confirm",
            ],
            "additionalProperties": False,
        },
        "strict": True,
    }
)


def ai_execute_tool(user_id: int, name: str, args: dict):
    if name == "open_trade":
        return ai_open_trade(user_id, args)
    if name == "add_entry":
        return ai_add_entry(user_id, args)
    if name == "record_trade_event":
        return ai_record_trade_event(user_id, args)
    if name == "close_trade":
        return ai_close_trade(user_id, args)
    if name == "get_trade_state":
        return ai_get_trade_state(user_id, args)
    if name == "get_journal_history":
        return ai_get_journal_history(user_id, args)
    if name == "edit_journal":
        return ai_edit_journal(user_id, args)

    if name == "delete_journal":
        return ai_delete_journal(user_id, args)

    if name == "delete_trade":
        return ai_delete_trade(user_id, args)

    return {"ok": False, "error": f"Unknown tool: {name}"}


def ai_run_turn(user_id: int, user_text: str):
    init_ai_db()

    history = ai_recent_messages(user_id, limit=10)
    member_state = ai_member_context(user_id)

    input_items = [
        {"role": item["role"], "content": item["content"]}
        for item in history
    ]
    input_items.append({"role": "user", "content": user_text})

    instructions = GTOP_AI_PROMPT + "\n\n" + member_state

    response = ai_client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=input_items,
        tools=GBOP_AI_TOOLS,
        store=False,
    )

    for _ in range(5):
        calls = [
            item
            for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]

        if not calls:
            return response.output_text.strip()

        input_items += response.output

        for call in calls:
            try:
                args = json.loads(call.arguments)
                result = ai_execute_tool(user_id, call.name, args)
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

            input_items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result),
            })

        response = ai_client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=input_items,
            tools=GBOP_AI_TOOLS,
            store=False,
        )

    return (
        "I hit the internal action limit for this turn. "
        "Tell me what you want to do next."
    )


async def ai_send_chunks(destination, text: str):
    text = (text or "").strip()
    if not text:
        text = "I couldn't generate a response for that."

    while text:
        if len(text) <= 1900:
            await destination.send(text)
            break

        split_at = text.rfind("\n", 0, 1900)
        if split_at < 800:
            split_at = text.rfind(" ", 0, 1900)
        if split_at < 800:
            split_at = 1900

        await destination.send(text[:split_at])
        text = text[split_at:].lstrip()


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Natural conversation works in DMs, or by @mentioning GBOP in G.T.O.P.
    # This deliberately avoids reading unrelated server messages.
    is_dm = message.guild is None
    is_mentioned = (
        client.user is not None
        and client.user in message.mentions
    )

    if not is_dm and not is_mentioned:
        return

    member = await ai_resolve_member(message.author.id)

    if member is None:
        await message.reply(
            "I can only use GTOP member data for people currently in G.T.O.P."
        )
        return

    ensure_member_record(member)
    record = get_member_record(member.id)

    if not is_owner(member):
        if not has_member_role(member):
            await message.reply(
                "You don't currently have the GTOP member role required "
                "to use GBOP."
            )
            return

        if record["revoked"]:
            await message.reply("Your GBOP access is currently revoked.")
            return

        if not record["activated"]:
            await message.reply(
                "Activate your GBOP profile in G.T.O.P first with "
                "`/activate agree:true`."
            )
            return

    content = message.content or ""

    if client.user is not None:
        content = content.replace(f"<@{client.user.id}>", "")
        content = content.replace(f"<@!{client.user.id}>", "")

    content = content.strip()

    if not content:
        await message.reply(
            "I'm here. Talk to me normally about your GTOP trade, "
            "journal, risk, or playbook."
        )
        return

    init_ai_db()
    ai_save_message(member.id, "user", content)

    try:
        async with message.channel.typing():
            answer = await asyncio.to_thread(
                ai_run_turn,
                member.id,
                content,
            )
    except Exception as exc:
        await message.reply(
            "I hit an AI connection error. Your existing GBOP trade and "
            "journal records were not changed. "
            f"`{type(exc).__name__}`"
        )
        return

    ai_save_message(member.id, "assistant", answer)

    if len(answer) <= 1900:
        await message.reply(answer)
    else:
        await message.reply(answer[:1900])
        await ai_send_chunks(
            message.channel,
            answer[1900:].lstrip(),
        )






# -----------------------------
# GBOP VOICE FINAL V5
# -----------------------------

GBOP_REALTIME_MODEL = os.getenv("GBOP_REALTIME_MODEL", "gpt-realtime-2.1-mini")
GBOP_REALTIME_VOICE = os.getenv("GBOP_REALTIME_VOICE", "marin")

GBOP_RT_SESSIONS = {}
GBOP_RT_OUTPUT_MANAGERS = {}
GBOP_RT_SINKS = {}
GBOP_RT_LOCKS = {}


def gbop_voice_member_allowed(member):
    ensure_member_record(member)
    record = get_member_record(member.id)

    if is_owner(member):
        return True, None
    if not has_member_role(member):
        return False, "Missing GTOP member role."
    if record["revoked"]:
        return False, "GBOP access is revoked."
    if not record["activated"]:
        return False, "GBOP profile is not activated."
    return True, None


def gbop_safety_identifier(user_id: int):
    return hashlib.sha256(f"gtop-discord-user:{user_id}".encode()).hexdigest()


def gbop_pcm48_stereo_to_pcm24_mono(pcm: bytes):
    if not pcm:
        return b""

    src = array("h")
    src.frombytes(pcm)

    if sys.byteorder != "little":
        src.byteswap()

    usable = len(src) - (len(src) % 4)
    out = array("h")

    for i in range(0, usable, 4):
        value = (
            int(src[i])
            + int(src[i + 1])
            + int(src[i + 2])
            + int(src[i + 3])
        ) // 4

        value = max(-32768, min(32767, value))
        out.append(value)

    if sys.byteorder != "little":
        out.byteswap()

    return out.tobytes()


def gbop_pcm24_mono_to_pcm48_stereo(pcm: bytes):
    if not pcm:
        return b""

    src = array("h")
    src.frombytes(pcm)

    if sys.byteorder != "little":
        src.byteswap()

    if not src:
        return b""

    out = array("h")
    previous = int(src[0])

    for raw in src:
        current = int(raw)
        mid = (previous + current) // 2
        out.extend((mid, mid, current, current))
        previous = current

    if sys.byteorder != "little":
        out.byteswap()

    return out.tobytes()


class GBOPRealtimeAudioSource(discord.AudioSource):
    FRAME_BYTES = 3840
    BYTES_PER_MS = 48000 * 2 * 2 / 1000

    def __init__(self):
        self.buffer = bytearray()
        self.condition = threading.Condition()
        self.finished = False
        self.aborted = False
        self.played_bytes = 0

    def is_opus(self):
        return False

    @property
    def played_ms(self):
        return int(self.played_bytes / self.BYTES_PER_MS)

    def feed(self, pcm24_mono: bytes):
        pcm48 = gbop_pcm24_mono_to_pcm48_stereo(pcm24_mono)
        if not pcm48:
            return

        with self.condition:
            if self.finished or self.aborted:
                return
            self.buffer.extend(pcm48)
            self.condition.notify_all()

    def finish(self):
        with self.condition:
            self.finished = True
            self.condition.notify_all()

    def abort(self):
        with self.condition:
            self.aborted = True
            self.buffer.clear()
            self.condition.notify_all()

    def read(self):
        with self.condition:
            while (
                len(self.buffer) < self.FRAME_BYTES
                and not self.finished
                and not self.aborted
            ):
                self.condition.wait(timeout=0.25)

            if self.aborted:
                return b""

            if len(self.buffer) >= self.FRAME_BYTES:
                chunk = bytes(self.buffer[: self.FRAME_BYTES])
                del self.buffer[: self.FRAME_BYTES]
                self.played_bytes += len(chunk)
                return chunk

            if self.finished and self.buffer:
                chunk = bytes(self.buffer)
                self.buffer.clear()
                if len(chunk) < self.FRAME_BYTES:
                    chunk += b"\x00" * (self.FRAME_BYTES - len(chunk))
                self.played_bytes += len(chunk)
                return chunk

            return b""

    def cleanup(self):
        self.abort()


class GBOPOutputManager:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.lock = asyncio.Lock()
        self.source = None
        self.session = None
        self.voice_client = None
        self.item_id = None

    async def interrupt(self):
        async with self.lock:
            source = self.source
            session = self.session
            voice_client = self.voice_client
            item_id = self.item_id

            played_ms = source.played_ms if source is not None else 0

            if source is not None:
                source.abort()

            if voice_client is not None and voice_client.is_playing():
                try:
                    if hasattr(voice_client, "stop_playing"):
                        voice_client.stop_playing()
                    else:
                        discord.VoiceClient.stop(voice_client)
                except Exception as exc:
                    print("[GBOP-RT] playback stop error:", type(exc).__name__, exc)

            if session is not None:
                await session.send_event({"type": "response.cancel"}, quiet=True)

                if item_id and played_ms > 0:
                    await session.send_event(
                        {
                            "type": "conversation.item.truncate",
                            "item_id": item_id,
                            "content_index": 0,
                            "audio_end_ms": played_ms,
                        },
                        quiet=True,
                    )

            self.source = None
            self.session = None
            self.voice_client = None
            self.item_id = None

    async def begin(self, session, voice_client, item_id):
        await self.interrupt()

        async with self.lock:
            source = GBOPRealtimeAudioSource()
            self.source = source
            self.session = session
            self.voice_client = voice_client
            self.item_id = item_id

            loop = asyncio.get_running_loop()

            def after_playback(error):
                loop.call_soon_threadsafe(self._playback_done, source, error)

            voice_client.play(source, after=after_playback)
            return source

    def _playback_done(self, source, error):
        if error:
            print("[GBOP-RT] Discord playback error:", repr(error))

        if self.source is source:
            self.source = None
            self.session = None
            self.voice_client = None
            self.item_id = None


def gbop_output_manager(guild_id: int):
    manager = GBOP_RT_OUTPUT_MANAGERS.get(guild_id)
    if manager is None:
        manager = GBOPOutputManager(guild_id)
        GBOP_RT_OUTPUT_MANAGERS[guild_id] = manager
    return manager


class GBOPRealtimeSession:
    def __init__(self, member: discord.Member, voice_client, loop):
        self.member = member
        self.voice_client = voice_client
        self.loop = loop
        self.websocket = None
        self.audio_queue = asyncio.Queue(maxsize=400)
        self.ready = asyncio.Event()
        self.closed = False
        self.runner = None
        self.last_error = None
        self.output_source = None
        self.output_item_id = None
        self.tool_output_pending = False

    def instructions(self):
        member_state = ai_member_context(self.member.id)

        return (
            GTOP_AI_PROMPT
            + "\n\nLIVE DISCORD VOICE MODE\n"
            + "- This voice channel is in dedicated GBOP mode. Treat authorized member speech as addressed to you.\n"
            + "- Respond naturally and quickly. Default to 1-3 short sentences unless detail is requested.\n"
            + "- Do not read markdown, headings, tables, or long lists aloud.\n"
            + "- If exactly one fact is missing for an action, ask only for that fact.\n"
            + "- Never claim a database action occurred unless its tool returned success.\n"
            + "- Custom plays and custom entry models are valid.\n"
            + "- Risk violations are warn-and-save.\n"
            + "- A member may interrupt while you speak. Stop the old thought and answer the new one.\n\n"
            + member_state
        )

    def session_update(self):
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": GBOP_REALTIME_MODEL,
                "output_modalities": ["audio"],
                "instructions": self.instructions(),
                "tools": [{k: v for k, v in tool.items() if k != "strict"} for tool in GBOP_AI_TOOLS],
                "tool_choice": "auto",
                "reasoning": {"effort": "low"},
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.45,
                            "prefix_padding_ms": 250,
                            "silence_duration_ms": 350,
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "voice": GBOP_REALTIME_VOICE,
                    },
                },
            },
        }

    async def connect_ws(self):
        url = f"wss://api.openai.com/v1/realtime?model={GBOP_REALTIME_MODEL}"

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Safety-Identifier": gbop_safety_identifier(self.member.id),
        }

        kwargs = {
            "max_size": None,
            "compression": None,
            "ping_interval": 20,
            "ping_timeout": 20,
        }

        try:
            return await websockets.connect(
                url,
                additional_headers=headers,
                **kwargs,
            )
        except TypeError:
            return await websockets.connect(
                url,
                extra_headers=headers,
                **kwargs,
            )

    async def send_event(self, event, quiet=False):
        if self.websocket is None:
            return False

        try:
            await self.websocket.send(json.dumps(event))
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if not quiet:
                print("[GBOP-RT] send error:", self.last_error)
            return False

    def enqueue_audio(self, pcm24_mono: bytes):
        if self.closed or not pcm24_mono:
            return

        try:
            self.audio_queue.put_nowait(pcm24_mono)
        except asyncio.QueueFull:
            try:
                self.audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

            try:
                self.audio_queue.put_nowait(pcm24_mono)
            except asyncio.QueueFull:
                pass

    async def sender_loop(self):
        while not self.closed:
            pcm = await self.audio_queue.get()
            ok = await self.send_event(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                },
                quiet=True,
            )
            if not ok:
                raise RuntimeError("Realtime audio send failed.")

    async def refresh_context(self):
        await self.send_event(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": self.instructions(),
                },
            },
            quiet=True,
        )

    async def execute_tool(self, item):
        name = item.get("name")
        call_id = item.get("call_id")

        if not name or not call_id:
            return

        try:
            args = json.loads(item.get("arguments", "{}"))
        except Exception:
            args = {}

        print("[GBOP-RT] tool call:", name, args)

        try:
            result = await asyncio.to_thread(
                ai_execute_tool,
                self.member.id,
                name,
                args,
            )
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        await self.send_event(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result),
                },
            }
        )

        self.tool_output_pending = True
        await self.refresh_context()

    async def receiver_loop(self):
        async for raw in self.websocket:
            try:
                event = json.loads(raw)
            except Exception:
                continue

            event_type = event.get("type", "")

            if event_type == "session.updated":
                self.ready.set()
                continue

            if event_type == "error":
                error = event.get("error", {})
                self.last_error = error.get("message") or str(error)
                print("[GBOP-RT] API error:", self.last_error)
                continue

            if event_type == "input_audio_buffer.speech_started":
                await gbop_output_manager(self.member.guild.id).interrupt()
                continue

            if event_type == "response.output_audio.delta":
                delta = event.get("delta")
                if not delta:
                    continue

                try:
                    pcm24 = base64.b64decode(delta)
                except Exception:
                    continue

                item_id = event.get("item_id")
                manager = gbop_output_manager(self.member.guild.id)

                if self.output_source is None or self.output_item_id != item_id:
                    self.output_source = await manager.begin(
                        self,
                        self.voice_client,
                        item_id,
                    )
                    self.output_item_id = item_id

                self.output_source.feed(pcm24)
                continue

            if event_type == "response.output_audio.done":
                if self.output_source is not None:
                    self.output_source.finish()
                self.output_source = None
                self.output_item_id = None
                continue

            if event_type == "response.output_audio_transcript.done":
                transcript = (event.get("transcript", "") or "").strip()
                if transcript:
                    ai_save_message(self.member.id, "assistant", transcript)
                continue

            if event_type == "response.output_item.done":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    await self.execute_tool(item)
                continue

            if event_type == "response.done":
                response = event.get("response") or {}
                status = response.get("status")

                if self.tool_output_pending and status not in ("cancelled", "failed"):
                    self.tool_output_pending = False
                    await self.send_event({"type": "response.create"})

    async def run(self):
        backoff = 1.0

        while not self.closed:
            self.ready.clear()

            try:
                print("[GBOP-RT] connecting:", self.member, GBOP_REALTIME_MODEL)
                self.websocket = await self.connect_ws()
                self.last_error = None

                receiver = asyncio.create_task(self.receiver_loop())

                await self.send_event(self.session_update())
                await asyncio.wait_for(self.ready.wait(), timeout=10)

                sender = asyncio.create_task(self.sender_loop())

                print("[GBOP-RT] READY:", self.member)

                done, pending = await asyncio.wait(
                    {receiver, sender},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()

                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        raise exc

                backoff = 1.0

            except asyncio.CancelledError:
                break

            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print("[GBOP-RT] session error:", self.member, self.last_error)

                if self.closed:
                    break

                await asyncio.sleep(backoff)
                backoff = min(8.0, backoff * 2)

            finally:
                ws = self.websocket
                self.websocket = None
                self.ready.clear()

                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    async def close(self):
        self.closed = True

        if self.runner is not None:
            self.runner.cancel()

        if self.output_source is not None:
            self.output_source.abort()

        ws = self.websocket
        self.websocket = None

        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


class GBOPRealtimeManager:
    def __init__(self):
        self.sessions = {}

    def key(self, member):
        return (member.guild.id, member.id)

    async def get_session(self, member, voice_client, loop):
        key = self.key(member)
        session = self.sessions.get(key)

        if session is not None and not session.closed:
            session.voice_client = voice_client
            return session

        lock = GBOP_RT_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            GBOP_RT_LOCKS[key] = lock

        async with lock:
            session = self.sessions.get(key)

            if session is not None and not session.closed:
                session.voice_client = voice_client
                return session

            session = GBOPRealtimeSession(member, voice_client, loop)
            self.sessions[key] = session
            session.runner = asyncio.create_task(session.run())
            return session

    async def preconnect_channel(self, voice_client, loop):
        for member in getattr(voice_client.channel, "members", []):
            if member.bot:
                continue

            try:
                allowed, _ = gbop_voice_member_allowed(member)
            except Exception:
                allowed = False

            if allowed:
                await self.get_session(member, voice_client, loop)

    async def feed(self, member, voice_client, pcm24, loop):
        try:
            allowed, _ = gbop_voice_member_allowed(member)
        except Exception:
            return

        if not allowed:
            return

        session = await self.get_session(member, voice_client, loop)
        session.enqueue_audio(pcm24)

    async def close_guild(self, guild_id: int):
        keys = [key for key in self.sessions if key[0] == guild_id]

        for key in keys:
            session = self.sessions.pop(key, None)
            if session is not None:
                await session.close()

        manager = GBOP_RT_OUTPUT_MANAGERS.pop(guild_id, None)
        if manager is not None:
            await manager.interrupt()


GBOP_REALTIME_MANAGER = GBOPRealtimeManager()


class GBOPRealtimeSink(voice_recv.AudioSink):
    def __init__(self, loop, voice_client):
        super().__init__()
        self.loop = loop
        self.voice_client_ref = voice_client
        self.closed = False

    def wants_opus(self):
        return False

    def write(self, user, data):
        if self.closed:
            return

        if isinstance(user, discord.Member):
            member = user
        else:
            member = self.voice_client_ref.guild.get_member(
                getattr(user, "id", 0)
            )

        if member is None or member.bot:
            return

        pcm48 = getattr(data, "pcm", None)
        if not pcm48:
            return

        pcm24 = gbop_pcm48_stereo_to_pcm24_mono(pcm48)
        if not pcm24:
            return

        asyncio.run_coroutine_threadsafe(
            GBOP_REALTIME_MANAGER.feed(
                member,
                self.voice_client_ref,
                pcm24,
                self.loop,
            ),
            self.loop,
        )

    def cleanup(self):
        self.closed = True


def gbop_start_realtime_listener(voice_client):
    if not isinstance(voice_client, voice_recv.VoiceRecvClient):
        raise RuntimeError(
            "GBOP is not connected with a receive-capable voice client."
        )

    if voice_client.is_listening():
        voice_client.stop_listening()

    loop = asyncio.get_running_loop()
    sink = GBOPRealtimeSink(loop, voice_client)

    GBOP_RT_SINKS[voice_client.guild.id] = sink

    def after(error):
        print("[GBOP-RT] Discord receive listener ended:", repr(error))

    voice_client.listen(sink, after=after)

    asyncio.create_task(
        GBOP_REALTIME_MANAGER.preconnect_channel(
            voice_client,
            loop,
        )
    )

    print("[GBOP-RT] realtime Discord audio bridge ACTIVE")


async def gbop_voice_health_text(interaction):
    guild = interaction.guild
    vc = guild.voice_client if guild else None

    session = None
    if guild is not None:
        session = GBOP_REALTIME_MANAGER.sessions.get(
            (guild.id, interaction.user.id)
        )

    return "\n".join(
        [
            "**GBOP Realtime Voice Health**",
            f"Discord connected: **{bool(vc and vc.is_connected())}**",
            (
                f"Receive-capable: "
                f"**{isinstance(vc, voice_recv.VoiceRecvClient) if vc else False}**"
            ),
            (
                f"Discord listening: "
                f"**{bool(vc and isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_listening())}**"
            ),
            (
                f"Voice sink: "
                f"**{type(GBOP_RT_SINKS.get(guild.id)).__name__ if guild and GBOP_RT_SINKS.get(guild.id) else 'None'}**"
            ),
            f"Realtime model: **{GBOP_REALTIME_MODEL}**",
            f"Realtime voice: **{GBOP_REALTIME_VOICE}**",
            f"Your session exists: **{session is not None}**",
            f"Your WebSocket ready: **{bool(session and session.ready.is_set())}**",
            (
                f"Your last error: "
                f"**{session.last_error if session and session.last_error else 'None'}**"
            ),
        ]
    )


@tree.command(
    name="voice",
    description="Start GBOP's live Realtime AI in your current voice channel.",
    guild=GUILD,
)
async def voice(interaction: discord.Interaction):
    if not await require_member(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    member = interaction.user
    voice_state = getattr(member, "voice", None)
    channel = voice_state.channel if voice_state else None

    if channel is None:
        await interaction.followup.send(
            "Join a Discord voice channel first, then run `/voice`.",
            ephemeral=True,
        )
        return

    try:
        vc = interaction.guild.voice_client

        if vc is not None and not isinstance(vc, voice_recv.VoiceRecvClient):
            await vc.disconnect(force=True)
            vc = None

        if vc is None:
            vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        elif vc.channel.id != channel.id:
            await vc.move_to(channel)

        gbop_start_realtime_listener(vc)

    except Exception as exc:
        await interaction.followup.send(
            (
                "❌ GBOP could not start Realtime voice.\n"
                f"`{type(exc).__name__}: {exc}`"
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        (
            f"🎙️ **GBOP Realtime AI is active in {channel.mention}.**\n\n"
            "While `/voice` is active, speech from authorized GTOP members "
            "in this voice channel is treated as conversation with GBOP and "
            "is streamed to OpenAI in real time. GBOP does not intentionally "
            "save the raw audio.\n\n"
            "Speak naturally—no wake word is needed. If GBOP is talking, "
            "just interrupt it by speaking."
        ),
        ephemeral=True,
    )


@tree.command(
    name="voiceoff",
    description="Stop GBOP Realtime voice and leave the voice channel.",
    guild=GUILD,
)
async def voiceoff(interaction: discord.Interaction):
    if not await require_member(interaction):
        return

    await interaction.response.defer(ephemeral=True, thinking=False)

    guild = interaction.guild
    vc = guild.voice_client if guild else None

    if vc is None:
        await interaction.followup.send(
            "GBOP is not connected to voice.",
            ephemeral=True,
        )
        return

    try:
        if isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_listening():
            vc.stop_listening()

        await GBOP_REALTIME_MANAGER.close_guild(guild.id)
        await vc.disconnect(force=True)

    except Exception as exc:
        await interaction.followup.send(
            (
                "Voice shutdown hit an error:\n"
                f"`{type(exc).__name__}: {exc}`"
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        "🔇 GBOP Realtime voice stopped.",
        ephemeral=True,
    )


@tree.command(
    name="voicehealth",
    description="Show GBOP Realtime voice diagnostics.",
    guild=GUILD,
)
async def voicehealth(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=False)

    member = interaction.user
    if not (is_owner(member) or has_member_role(member)):
        await interaction.followup.send(
            "You do not have access to GBOP voice diagnostics.",
            ephemeral=True,
        )
        return

    try:
        text = await gbop_voice_health_text(interaction)
    except Exception as exc:
        text = f"❌ Voice health failed:\n`{type(exc).__name__}: {exc}`"

    await interaction.followup.send(text, ephemeral=True)


@tree.command(
    name="realtimehealth",
    description="Alias for GBOP Realtime voice diagnostics.",
    guild=GUILD,
)
async def realtimehealth(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=False)

    try:
        text = await gbop_voice_health_text(interaction)
    except Exception as exc:
        text = f"❌ Realtime health failed:\n`{type(exc).__name__}: {exc}`"

    await interaction.followup.send(text, ephemeral=True)


@tree.error
async def gbop_tree_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    original = getattr(error, "original", error)

    print(
        "[GBOP-COMMAND-ERROR]",
        getattr(interaction.command, "name", "unknown"),
        type(original).__name__,
        original,
    )

    try:
        message = (
            "❌ **GBOP command error**\n"
            f"`{type(original).__name__}: {original}`"
        )

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


if __name__ == "__main__":
    print("[GBOP-STARTUP] bot.py launched; connecting to Discord.")
    client.run(DISCORD_TOKEN)
