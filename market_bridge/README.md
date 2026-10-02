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
