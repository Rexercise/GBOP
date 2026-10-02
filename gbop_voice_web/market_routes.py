"""Bounded, authenticated ingestion; market reads use existing member auth."""
import asyncio
import json
import os
from fastapi import APIRouter, HTTPException, Request
from gbop_voice_web.market_data import MAX_BYTES, authorized, ingest, market_tool


def market_router(db, authenticate):
    router = APIRouter()

    @router.post('/api/market/ingest')
    async def receive(request: Request):
        if len(os.getenv('GBOP_MARKET_BRIDGE_TOKEN', '')) < 32:
            raise HTTPException(503, 'Market bridge is not configured.')
        if not authorized(request.headers.get('authorization')):
            raise HTTPException(401, 'Invalid bridge credentials.')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise HTTPException(413, 'Bridge payload too large.')
        try:
            data = json.loads(body)
            return await asyncio.to_thread(ingest, db, data)
        except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/api/market/{asset}')
    async def price(asset: str, request: Request):
        await authenticate(request)
        return await asyncio.to_thread(market_tool, db, 'get_market_price', {'asset': asset})

    return router
