"""One owner-confirmed readiness campaign using existing private delivery storage.

No startup sends or schema changes. The sentinel user_id=0 stores the immutable
campaign manifest; actual Discord recipients have positive IDs. All records use
a dedicated event_key, separate from scheduled shift delivery keys.
"""
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json

from gbop_voice_web.owner_broadcast import owner_message

CAMPAIGN = 'readiness:journal-2026-10-21'
TEXT = 'I’m ready and available to help you journal. Message me to record a trade or continue your journal.'
MESSAGE = owner_message(TEXT)
BODY_HASH = hashlib.sha256(MESSAGE.encode('utf-8')).hexdigest()
STATES = {'queued', 'pending', 'sent', 'blocked', 'uncertain', 'rate_limited', 'skipped'}
CAMPAIGN_TIMEOUT_SECONDS = 300


def bind_draft(draft):
    if (draft.audience not in ('all', 'all_except') or draft.image is not None
            or draft.message != MESSAGE):
        raise ValueError('The readiness announcement uses its fixed message and eligible GBOP members only.')
    ids = tuple(r.user_id for r in draft.recipients)
    if (not ids or len(set(ids)) != len(ids)
            or any(type(uid) is not int or not 0 < uid < 2**63 for uid in ids)):
        raise ValueError('The readiness audience could not be verified.')
    return replace(draft, campaign=CAMPAIGN)


@dataclass(frozen=True)
class Receipt:
    state: str
    message_id: str = ''
    channel_id: str = ''


@dataclass(frozen=True)
class Result:
    sent: int = 0
    blocked: int = 0
    uncertain: int = 0
    skipped: int = 0
    queued: int = 0
    rate_limited: int = 0
    previous: bool = False
    unavailable: bool = False

    def summary(self):
        if self.unavailable:
            return ('The readiness announcement stopped because its eligibility or delivery records '
                    'could not be verified. No deliveries will be retried automatically. '
                    'Check the campaign status before taking further action.')
        prefix = ('This readiness announcement has already started. No new deliveries were attempted. '
                  if self.previous else 'Readiness announcement results: ')
        return (prefix + f'**{self.sent}** confirmed delivered; {self.blocked} blocked; '
                f'{self.uncertain} uncertain; {self.rate_limited} rate-limited; '
                f'{self.skipped} no longer eligible; {self.queued} not attempted. '
                'This campaign will not resend or add recipients.')


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _payload(state, message_id='', channel_id=''):
    return _encoded(dict(state=state, message_id=message_id, channel_id=channel_id, body_sha256=BODY_HASH))


def _now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(db, guild_id, owner_id, *, previous=False):
    with db() as conn:
        rows = conn.execute('''SELECT user_id,discord_message_id FROM gbop_shift_deliveries
            WHERE event_key=? AND guild_id=?''', (CAMPAIGN, guild_id)).fetchall()
    if not rows:
        return None
    values = {row['user_id']: json.loads(row['discord_message_id']) for row in rows}
    manifest = values.pop(0, {})
    if (manifest.get('version') != 1 or manifest.get('owner_id') != owner_id
            or manifest.get('body_sha256') != BODY_HASH or manifest.get('count') != len(values)):
        raise ValueError('Campaign manifest mismatch')
    counts = dict(sent=0, blocked=0, uncertain=0, skipped=0, queued=0, rate_limited=0)
    for uid, value in values.items():
        state = value.get('state')
        if (type(uid) is not int or uid <= 0 or value.get('body_sha256') != BODY_HASH
                or state not in STATES):
            raise ValueError('Campaign receipt mismatch')
        if state == 'sent' and not (_snowflake(value.get('message_id')) and _snowflake(value.get('channel_id'))):
            raise ValueError('Campaign receipt missing Discord identity')
        counts['uncertain' if state == 'pending' else state] += 1
    return Result(**counts, previous=previous)


def claim_campaign(db, draft):
    """Atomically freeze the entire reviewed audience before any DM request."""
    manifest = _encoded(dict(version=1, owner_id=draft.owner_id,
                             body_sha256=BODY_HASH, count=len(draft.recipients)))
    sql = '''INSERT INTO gbop_shift_deliveries
        (event_key,guild_id,user_id,discord_message_id,delivered_at) VALUES (?,?,?,?,?)
        ON CONFLICT (event_key,guild_id,user_id) DO NOTHING RETURNING user_id'''
    with db() as conn:
        inserted = conn.execute(sql, (CAMPAIGN, draft.guild_id, 0, manifest, _now())).fetchone()
        if inserted is None:
            return False
        for recipient in draft.recipients:
            row = conn.execute(sql, (CAMPAIGN, draft.guild_id, recipient.user_id,
                                    _payload('queued'), _now())).fetchone()
            if row is None:
                raise ValueError('Unexpected existing campaign receipt')
    return True


def transition(db, guild_id, user_id, before, receipt):
    with db() as conn:
        row = conn.execute('''UPDATE gbop_shift_deliveries SET discord_message_id=?,delivered_at=?
            WHERE event_key=? AND guild_id=? AND user_id=? AND discord_message_id=?
            RETURNING user_id''', (_payload(receipt.state, receipt.message_id, receipt.channel_id),
                                  _now(), CAMPAIGN, guild_id, user_id, _payload(before))).fetchone()
        if row is None:
            raise ValueError('Campaign receipt changed unexpectedly')


async def deliver(db, draft, authorized, sender, *, logger, pause=asyncio.sleep):
    """Bound owner completion; interrupted pending claims remain non-retryable."""
    try:
        return await asyncio.wait_for(_deliver(db, draft, authorized, sender, pause=pause),
                                      timeout=CAMPAIGN_TIMEOUT_SECONDS)
    except Exception:
        # Never include exception bodies, tokens, recipient IDs or journal data.
        logger.warning('GBOP readiness campaign stopped; eligibility or durable receipt unavailable')
        return Result(unavailable=True)


async def _deliver(db, draft, authorized, sender, *, pause):
    """Use only the frozen preview IDs; a claimed campaign is never resumed."""
    async def check(uid):
        value = await asyncio.wait_for(authorized(uid), timeout=20)
        if type(value) is not bool:
            raise ValueError('Unverified recipient access')
        return value
    if draft.campaign != CAMPAIGN or bind_draft(draft) != draft:
        raise ValueError('Invalid readiness draft')
    saved = await asyncio.to_thread(snapshot, db, draft.guild_id, draft.owner_id, previous=True)
    if saved is not None:
        return saved
    # Complete fresh eligibility preflight. Unavailable checks raise; a known
    # revocation is skipped later and does not expand the reviewed audience.
    for recipient in draft.recipients:
        await check(recipient.user_id)
    await sender.verify_identity()
    if not await asyncio.to_thread(claim_campaign, db, draft):
        return await asyncio.to_thread(snapshot, db, draft.guild_id, draft.owner_id, previous=True)
    for recipient in draft.recipients:
        uid = recipient.user_id
        allowed = await check(uid)
        if not allowed:
            await asyncio.to_thread(transition, db, draft.guild_id, uid, 'queued', Receipt('skipped'))
            continue
        await asyncio.to_thread(transition, db, draft.guild_id, uid, 'queued', Receipt('pending'))
        # Close the claim-to-send access gap. An exception leaves a durable
        # pending/uncertain marker and stops all remaining deliveries.
        allowed = await check(uid)
        if not allowed:
            outcome = Receipt('skipped')
        else:
            try:
                outcome = await sender.send_once(uid, draft.message)
                if (outcome.state not in ('sent', 'blocked', 'uncertain', 'rate_limited', 'skipped')
                        or outcome.state == 'sent' and not (
                            _snowflake(outcome.message_id) and _snowflake(outcome.channel_id))):
                    outcome = Receipt('uncertain')
            except Exception:
                outcome = Receipt('uncertain')
        await asyncio.to_thread(transition, db, draft.guild_id, uid, 'pending', outcome)
        if outcome.state in ('uncertain', 'rate_limited'):
            break
        await pause(1)
    return await asyncio.to_thread(snapshot, db, draft.guild_id, draft.owner_id)


def _snowflake(value):
    return isinstance(value, str) and value.isdecimal() and 0 < int(value) < 2**64


class SingleAttemptDM:
    """Use the supported bot REST API without discord.py's internal POST retries."""
    def __init__(self, token, expected_bot_id, *, authorize, transport=None):
        import httpx
        self.expected_bot_id = str(expected_bot_id)
        self.authorize = authorize
        self.client = httpx.AsyncClient(
            base_url='https://discord.com/api/v10', headers={'Authorization': 'Bot ' + token},
            timeout=20, follow_redirects=False,
            transport=transport or httpx.AsyncHTTPTransport(retries=0))
        self.verified = False

    async def verify_identity(self):
        self.verified = False
        response = await self.client.get('/users/@me')
        response.raise_for_status()
        body = response.json()
        if body.get('bot') is not True or str(body.get('id')) != self.expected_bot_id:
            raise ValueError('GBOP identity mismatch')
        self.verified = True

    async def close(self):
        await self.client.aclose()

    @staticmethod
    def failure(response):
        if response.status_code == 429:
            return Receipt('rate_limited')
        return Receipt('blocked' if response.status_code in (403, 404) else 'uncertain')

    async def send_once(self, user_id, text):
        if not self.verified:
            raise ValueError('Bot identity not verified')
        try:
            response = await self.client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
            if response.status_code != 200:
                return self.failure(response)
            channel = response.json()
            recipients = channel.get('recipients', [])
            if (channel.get('type') != 1 or len(recipients) != 1
                    or str(recipients[0].get('id')) != str(user_id)
                    or not _snowflake(channel.get('id'))):
                return Receipt('uncertain')
            # Channel creation can be slow. Refresh access again directly before
            # the content POST; no message is sent after a known revocation.
            allowed = await asyncio.wait_for(self.authorize(user_id), timeout=20)
            if allowed is False:
                return Receipt('skipped')
            if allowed is not True:
                return Receipt('uncertain')
            response = await self.client.post('/channels/' + channel['id'] + '/messages',
                json={'content': text, 'allowed_mentions': {'parse': []}})
            if response.status_code != 200:
                return self.failure(response)
            body = response.json()
            if (body.get('channel_id') != channel['id'] or not _snowflake(body.get('id'))
                    or str(body.get('author', {}).get('id')) != self.expected_bot_id):
                return Receipt('uncertain')
            return Receipt('sent', body['id'], channel['id'])
        except Exception:
            return Receipt('uncertain')
