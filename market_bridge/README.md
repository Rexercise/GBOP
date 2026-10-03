# GBOP Market Bridge

Read-only MT5 market feed for GBOP. Sends bid/ask, original tick timestamps and up to 2,304 closed M5 candles per configured instrument every 30 seconds. Stores only the latest bounded snapshot per asset in the existing database. No broker password, account balance, position history, order submission, paid API, or added hosting service is used.

## Windows setup

1. Install MT5 from Exness's official website and Python 3 x64 from python.org. Sign into MT5 with the Exness **investor/read-only password**. Keep those credentials on Windows.
2. Download this repository to a stable local folder on the Windows VM. Copy `config.example.json` to `config.json`.
3. Confirm each exact symbol in MT5 Market Watch. Example suffixes are illustrative; delete unavailable assets or replace the mappings. Set `terminal_path` to the actual terminal64.exe path.
4. Set the same random 32+ character token in private `config.json` and Render's `GBOP_MARKET_BRIDGE_TOKEN`. Do not use a Discord, OpenAI, Supabase, or broker credential. Never commit config.json. The receiver stays disabled without a token.
5. Open PowerShell as the Windows user running MT5, enter this folder, and run `./install.ps1`. It verifies one real upload before registering the task. The first request may take longer if Render is waking.
6. Ask GBOP “What's the NAS100 price?” Confirm the exact broker symbol, timestamp, and `fresh` status; compare the quote with Market Watch. Ask “Review today's NAS100 day shift.” Missing hours must be reported as insufficient data.

The scheduled task runs at **Windows login** and restarts on failure. Disconnect RDP instead of signing out: MT5 and the collector continue in the existing user session. A reboot requires one Windows login; this installer does not set up auto-login or store a Windows password. Do not promise unattended reboot recovery until that is separately configured and tested.

No inbound bridge port is required. Only outbound HTTPS to GBOP and MT5's broker connection are used. Do not expose RDP broadly for this collector. VM eligibility, disk/network costs, quotas, and available free sizes must be verified in Azure before resource creation. This code does not provision paid infrastructure.

## What GBOP can report

- `get_market_price`: broker quote, original tick timestamp, feed capture age. A fresh upload cannot make an old tick “live.” Quotes older than 120 seconds are stale, including closed markets.
- `review_market_session`: New York time with DST; complete closed M5 bars aggregated into the 7/8/9 hourly ranges for day/night sessions. Reports 9ate8 and Young Lefty range-sweep candidates, both-side ambiguity, insufficient candles, and closure invalidation. Follows later complete 10/11 hourly closes for invalidation.
- Does **not** confirm CSD, Super Soup, Blessed Thief execution, SMT, all six CRT variants, GCT, CBDR, Monday's Range, or target-hit order. It does not automatically update member trades or send unsolicited alerts.
- Historical review covers only the rolling candles retained by the bridge (up to eight trading days, hard-capped at 14 calendar days). It is not a permanent historical market archive.

## Operations

`./.venv/Scripts/python.exe bridge.py --once` checks one capture and upload. `bridge.log` rotates at 1 MB with two backups. The loop reconnects MT5 and backs off to five minutes on failure. Exact symbol failures are logged without credentials. Synchronize Windows time; future timestamps are rejected rather than silently shifted.

Pause: `Stop-ScheduledTask -TaskName 'GBOP Market Bridge'`.
Resume: `Start-ScheduledTask -TaskName 'GBOP Market Bridge'`.
Remove task: `Unregister-ScheduledTask -TaskName 'GBOP Market Bridge'`.

The feed is shared market data for authorized GTOP members, never shared account data. Ingestion requires the bridge token; HTTP reads reuse Discord member authentication. Supabase's public Data API roles have no access to the table. Schema creation is serialized across GBOP's web and Discord startup.

## Repository completion and activation checklist

HTTPS uploads use certifi's Mozilla CA bundle, including on new Windows hosts
whose system certificate store is not yet populated. Certificate and hostname
verification remain enabled, and redirects remain blocked. If an older collector
reports `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`,
update `bridge.py` and `requirements.txt` from the same repository revision and
rerun `install.ps1`; retain the private `config.json`. Do not disable TLS checks.

The collector, bounded token-authenticated ingestion, shared market storage,
member-authenticated price reads, and market tools for Discord and web are wired
in the repository. Configuration checks reject unsupported asset keys and malformed
endpoints/tokens before connecting to MT5. Oversized multi-asset captures split into
bounded uploads; partial upload failures are retried on the next capture. Config
permissions are restricted before installation or the first upload.

Repository verification: `python -m unittest discover -s tests` and
`python -m compileall -q bot.py gbop_voice_web market_bridge`.
Tests use mocked MT5/HTTP and a local database; they do not establish a live feed.

Activation still requires approval/access outside GitHub:

1. Approve merging and deploying the reviewed changes to the existing GBOP service.
2. Configure the private receiver token on that service (same token as Windows).
3. Provide access to an existing eligible Windows host; no VM or paid resource is
   provisioned by this repository. Install MT5/Python, sign into MT5 using investor
   access, and verify the exact broker symbols.
4. Run the Windows installer and compare a fresh GBOP quote to MT5. Test the task
   after disconnecting RDP and again after reboot/login. Windows task execution,
   broker connectivity, receiver database permissions, and end-to-end live quotes
   must be verified on the actual host/service.

Until these checks succeed, describe the bridge as implemented, awaiting activation.
Do not describe it as live or unattended after reboot.
# Candle evidence upgrade (October 2026)

The server accepts existing M5 collectors and upgraded M1 collectors. Updating
Render alone does **not** update the copy of `bridge.py` on Windows. Replace that
file on the VM with the reviewed version from GitHub, preserve `config.json` and
the existing virtual environment, then restart the existing bridge process/task.
No new VM, paid plan, credential or trading permission is required.

The upgraded collector sends up to 14 calendar days of closed M1 and M5 candles
at startup and hourly, then a two-hour overlap every 30 seconds. It omits the oldest
three minutes of that window so bars remain within the receiver's 14-day limit
through its existing 180-second capture-age allowance, including split uploads.
The receiver's age limits and candle timestamps are unchanged. The server merges
history by broker symbol, timeframe and UTC day, retaining 90 calendar days as
data accumulates. History before the initial backfill is not retroactively known.
MT5 may return fewer bars while history downloads; the bot reports missing coverage.

Conversation behavior, shared by Discord text/voice and browser voice:

- “Did today's NAS 988 happen?” fetches the session evidence before answering.
- “When was that high purged?” uses the selected asset/range from context.
- “What was last week's Wednesday low, and when did it form?” reads the requested
  historical window, stating coverage and source precision.
- `review_market_crt` accepts arbitrary anchor starts/timeframes, with the GTOP
  Monthly→Daily, Weekly→H4, Daily→H1, H4→M15, H1→M5 mappings.
- M2/M3/M4 and other minute intervals can be aggregated from M1. M5 cannot be
  reverse-engineered into finer candles. Timestamps identify intervals, not ticks.
- MOB is canonical discretionary knowledge, not automated PD-array detection.

Daily, weekly and monthly boundaries must match the member's chart. Do not equate
New York midnight with a broker session boundary. Incomplete data and market
closures are explicitly reported; they do not prove that a setup did not happen.
