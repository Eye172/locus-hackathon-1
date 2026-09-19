"""Gemini on Vertex AI, paid from the Google Cloud credits, for the inspector, the description and the campus facts.

The AI Studio key (GEMINI_API_KEY) is the free tier: 500 requests a day per model and ~15 a minute - the inspector's
bottleneck. The $300 Google Cloud credits do not pay for AI Studio (accounts opened after 2 Mar 2026), but they do
pay for Gemini on Vertex AI, which takes a service-account token instead of an API key (an API key gets 401).

The service-account JSON (role Vertex AI User) lives in backend/secrets/ (git-ignored); VERTEX_CREDENTIALS may point
elsewhere. The request body is the same as AI Studio's; only the URL and the Authorization header differ.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from ..config import settings

log = logging.getLogger("campuslens.vertex")

_creds = None
_project: str | None = None
_lock = asyncio.Lock()


def credentials_path() -> Path | None:
    p = Path(settings.vertex_credentials) if settings.vertex_credentials else \
        settings.data_dir.parent / "secrets" / "vertex-sa.json"
    return p if p.is_file() else None


def available() -> bool:
    return credentials_path() is not None


def project() -> str | None:
    global _project
    if _project is None and available():
        try:
            _project = json.loads(credentials_path().read_text(encoding="utf-8")).get("project_id")
        except Exception:  # noqa: BLE001
            _project = None
    return _project


def url(model: str, location: str | None = None) -> str:
    loc = location or settings.vertex_location
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project()}/locations/{loc}/publishers/google/models/{model}:generateContent"


async def headers() -> dict:
    """Authorization with an OAuth token of the service account, refreshed a minute before it expires."""
    global _creds
    async with _lock:
        if _creds is None:
            from google.oauth2 import service_account
            _creds = service_account.Credentials.from_service_account_file(
                str(credentials_path()), scopes=["https://www.googleapis.com/auth/cloud-platform"])
        exp = _creds.expiry.timestamp() if _creds.expiry else 0.0
        if not _creds.valid or exp - time.time() < 60:
            import google.auth.transport.requests as gtr
            await asyncio.to_thread(_creds.refresh, gtr.Request())
    return {"Authorization": f"Bearer {_creds.token}"}
