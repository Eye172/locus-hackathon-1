"""Shared async HTTP client.

Two clients: a normal one and an "insecure" one used only as a retry when a public university
website serves an incomplete certificate chain (observed on farabi.university and kbtu.edu.kz).
The retry is logged, and only ever used for GET requests of public pages and images.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("campuslens.http")

_client: httpx.AsyncClient | None = None
_insecure: httpx.AsyncClient | None = None

DEFAULT_HEADERS = {
    "User-Agent": settings.user_agent,
    "Accept-Language": "ru,en;q=0.8,kk;q=0.7",
}


def _make(verify: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.source_timeout_s, connect=4.0),
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        verify=verify,
        limits=httpx.Limits(max_connections=60, max_keepalive_connections=20),
    )


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = _make(True)
    return _client


def insecure_client() -> httpx.AsyncClient:
    global _insecure
    if _insecure is None:
        _insecure = _make(False)
    return _insecure


async def get(url: str, *, params: dict | None = None, headers: dict | None = None,
              timeout: float | None = None, allow_insecure: bool = True) -> httpx.Response:
    kw: dict[str, Any] = {"params": params, "headers": headers}
    if timeout is not None:
        kw["timeout"] = timeout
    try:
        return await client().get(url, **kw)
    except httpx.ConnectError as e:
        if allow_insecure and "CERTIFICATE_VERIFY_FAILED" in str(e):
            log.warning("ssl chain incomplete, retrying without verification: %s", url[:120])
            return await insecure_client().get(url, **kw)
        # a dropped handshake on flaky Wi-Fi: one quick retry before giving up
        await asyncio.sleep(0.4)
        return await client().get(url, **kw)
    except httpx.ConnectTimeout:
        await asyncio.sleep(0.4)
        return await client().get(url, **kw)


async def get_json(url: str, **kw) -> Any:
    r = await get(url, **kw)
    r.raise_for_status()
    return r.json()


async def post(url: str, *, data: dict | None = None, json: Any = None, headers: dict | None = None,
               timeout: float | None = None) -> httpx.Response:
    kw: dict[str, Any] = {"data": data, "json": json, "headers": headers}
    if timeout is not None:
        kw["timeout"] = timeout
    return await client().post(url, **kw)


async def close() -> None:
    global _client, _insecure
    for c in (_client, _insecure):
        if c is not None:
            await c.aclose()
    _client = _insecure = None
