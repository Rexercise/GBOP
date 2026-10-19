"""Bounded, private command failure feedback without upstream response bodies."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import time

import discord


LOG = logging.getLogger(__name__)
MEETING_TIMEOUT_SECONDS = 60
FEEDBACK_TIMEOUT_SECONDS = 5
ACKNOWLEDGEMENT_TIMEOUT_SECONDS = 3

MEETING_ACKNOWLEDGEMENT = (
    "**Meeting request received.** I'll post your private room link or result below. "
    "If no result appears within 70 seconds, completion could not be confirmed. "
    "Please don't repeat the command while Discord is rate-limiting requests. "
    "You can open your [private browser session](https://gbop.onrender.com/) instead."
)


def is_discord_rate_limit(error):
    return (isinstance(error, discord.HTTPException) and error.status == 429
            or isinstance(error, discord.RateLimited))


def retry_after_seconds(error):
    value = getattr(error, "retry_after", None)
    if value is None:
        headers = getattr(getattr(error, "response", None), "headers", {})
        value = headers.get("Retry-After")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0


class MeetingCooldown(Exception):
    pass


class DiscordCooldown:
    """Process-wide meeting circuit; no timers, requests or automatic retries."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.until = 0
        self.failures = 0
        self.probing = False

    def remaining(self):
        return max(0, math.ceil(self.until - self.clock()))

    def check(self):
        if self.remaining() or self.probing:
            raise MeetingCooldown()
        # After a rate limit, only one member may probe recovery at a time.
        self.probing = bool(self.failures)
        return self.probing

    def record(self, error):
        if not is_discord_rate_limit(error):
            return
        self.failures += 1
        delay = max(min(300 * 2 ** min(self.failures - 1, 3), 1800), retry_after_seconds(error))
        self.until = max(self.until, self.clock() + delay)

    def recovered(self):
        self.until = 0
        self.failures = 0


def command_error_message(error, cooldown):
    if is_discord_rate_limit(error) or isinstance(error, MeetingCooldown):
        return ("Discord is temporarily rate-limiting GBOP. "
                f"Please wait at least {max(1, cooldown.remaining())} seconds before trying again. "
                "No automatic retry is running.")
    if isinstance(error, TimeoutError):
        return ("GBOP could not confirm completion in time. "
                "Check `/gbop action:status` before trying again.")
    if isinstance(error, discord.Forbidden):
        return ("Discord denied this action. Ask the server owner to check GBOP's permissions, "
                "then try again.")
    # Preserve this actionable local failure, never arbitrary exception text.
    if (isinstance(error, RuntimeError) and str(error) ==
            "All GBOP Discord voice slots are in use. Try again later or use browser voice."):
        return "All GBOP Discord voice slots are in use. Try again later or use the browser session."
    return ("GBOP could not complete this command. Please try again later. "
            "The failure has been logged.")


def log_failure(interaction, error, stage):
    # HTTPException.__str__ may contain HTML, tokens or private upstream data.
    LOG.warning("[GBOP-COMMAND-ERROR] command=%s stage=%s type=%s status=%s code=%s acknowledged=%s",
                getattr(getattr(interaction, "command", None), "name", "unknown"),
                stage, type(error).__name__, getattr(error, "status", None),
                getattr(error, "code", None), interaction.response.is_done())


async def acknowledge_private_meeting(interaction, cooldown):
    """A visible receipt survives even if Discord refuses every later update."""
    blocked = bool(cooldown.remaining() or cooldown.probing)
    message = (command_error_message(MeetingCooldown(), cooldown)
               if blocked else MEETING_ACKNOWLEDGEMENT)
    try:
        await asyncio.wait_for(
            interaction.response.send_message(message, ephemeral=True),
            timeout=ACKNOWLEDGEMENT_TIMEOUT_SECONDS)
    except Exception as error:
        cooldown.record(error)
        log_failure(interaction, error, "acknowledgement")
        # No setup and no second delivery attempt after an uncertain/failed receipt.
        return False
    print(f"[GBOP-MEETING] stage=acknowledged setup_allowed={not blocked}", flush=True)
    return not blocked


async def report_command_error(interaction, error, cooldown, *, view=None):
    cooldown.record(error)
    log_failure(interaction, error, "command")
    message = command_error_message(error, cooldown)
    response = interaction.response
    deferred = (response.is_done() and
                response.type == discord.InteractionResponseType.deferred_channel_message)
    if deferred:
        # Editing the original deferred reply explicitly clears the thinking state.
        attempts = [lambda: interaction.edit_original_response(content=message, view=view),
                    lambda: interaction.followup.send(message, ephemeral=True, view=view)]
    elif response.is_done():
        attempts = [lambda: interaction.followup.send(message, ephemeral=True, view=view)]
    else:
        attempts = [lambda: response.send_message(message, ephemeral=True, view=view)]
    for index, send in enumerate(attempts):
        try:
            await asyncio.wait_for(send(), timeout=FEEDBACK_TIMEOUT_SECONDS)
            return True
        except Exception as failure:
            cooldown.record(failure)
            log_failure(interaction, failure, "feedback" if index == 0 else "feedback-fallback")
            # Do not hit another route after a rate limit or an uncertain timeout.
            if is_discord_rate_limit(failure) or isinstance(failure, TimeoutError):
                break
    LOG.error("[GBOP-COMMAND-ERROR] feedback-undelivered command=%s",
              getattr(getattr(interaction, "command", None), "name", "unknown"))
    return False


async def run_private_meeting(interaction, start, lock, cooldown):
    probing = False
    try:
        # Includes waiting for another /meet from this member and every REST call.
        async with asyncio.timeout(MEETING_TIMEOUT_SECONDS):
            async with lock:
                probing = cooldown.check()
                failures = cooldown.failures
                await start(interaction)
                if cooldown.failures == failures:
                    cooldown.recovered()
    except Exception as error:
        await report_command_error(interaction, error, cooldown)
    finally:
        if probing:
            cooldown.probing = False
