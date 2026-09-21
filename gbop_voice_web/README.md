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
