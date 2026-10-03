# GBOP Voice Web

This is the low-latency voice front end for GBOP.

Architecture:
- Browser microphone -> WebRTC -> OpenAI GPT-Live-1
- GPT-Live-1 handles the natural voice conversation and interruption.
- Private GTOP data/actions use client delegation to this local backend.
- The backend reads/writes the existing ../gbop.db and uses the existing OpenAI API key from ../.env.

## Local test
From ~/GTOP-Bot-GBOP/gbop_voice_web:

    ./start.sh

Then open:

    http://localhost:8787

Microphone access works on localhost.

## Phone / iPhone
Mobile browsers require HTTPS for microphone access. Put this app behind an HTTPS host/tunnel.
A quick development option is Cloudflare Tunnel:

    cloudflared tunnel --url http://localhost:8787

Open the HTTPS URL Cloudflare prints on the phone.

## Current authentication
Owner-preview mode uses GBOP_VOICE_PIN from ../.env and the Discord owner user ID
(GTOP_OWNER_USER_ID by default). The API key never goes to the browser.

For multi-member production use, add Discord OAuth and map each signed-in Discord user
to their own GBOP user_id. The backend code is already user-id scoped.


## Handwritten journals and coaching

Discord DMs accept up to four PNG/JPEG/WebP photos (8 MB each). Send a journal
page and say “Add this to my journal.” GBOP transcribes readable details, saves
separate entries for separate trades, preserves the original image, and leaves
unknown results blank. The saved review can be corrected conversationally:
“Journal #2 was Model 1, Tier 2.” Do not use hypothetical/study pages as trade
outcomes. Handwriting extraction is model-dependent; review unclear numbers.

Available through Discord text, Discord voice tools, and browser voice backend:

- “Show my textbook Super Soup examples.”
- “Send the picture for Journal #2.” (Delivery is to the requesting member's DM.)
- “Compare my entry models” or “Review my last seven days.”
- “Save my Day Shift plan for 2026-09-30: …”
- “Compare my plan with my journal.”

Statistics use one journal outcome per linked trade, exclude study/reflection
entries and unknown outcomes from win-rate denominators, and label mixed-entry
trades instead of crediting every model with the full result. Undated handwritten
trades are excluded from date-window reports. Dates use UTC for existing records;
handwritten dates are preserved as recorded. Weekly reviews are on request, not
scheduled broadcasts. The model asks only for missing or ambiguous details.

Conversational journals can retain the exact authenticated market review and
selected candle when the member identifies it as their trade. Reported entry and
exit timestamps stay separate from logging time; a candle interval is not an
exact fill time. Unknown R remains unknown, including for a reported stop-out.
Existing-journal corrections preserve their original association and record
provenance. Current-market snapshots retain their original as-of/cutoff when
carried into a journal. Normal recall returns a bounded factual summary; detailed
source evidence stays saved, and full journal prose can be privately delivered.

`review_current_market` handles on-demand current/pre-shift/in-shift/off-shift
analysis separately from completed shift recaps. It exposes source precision,
snapshot freshness and forming references; it never substitutes a completed
historical shift or infers instantaneous tick action. Explicit chart anchors can
use the existing supported timeframe mappings. Default shift recaps are short,
with independent relevant Young Lefty context and named-range details on request.

`journal_coach.init_coach` installs additive, idempotent tables. New tables have
RLS enabled and no anon/authenticated grants: only the trusted backend accesses
them, enforcing the guild/user identity supplied by the authenticated session.
Import retries use `(guild,user,photo,entry_index)` to update the same journal.
Deleting a journal removes its metadata; its source image remains in the member's
photo library. No new execution is created by a handwritten journal import.
