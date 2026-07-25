# -*- coding: utf-8 -*-
"""Minimal loopback-only FastAPI host for the public Lingxing Agent."""
from __future__ import annotations

import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from agent.security import load_or_create_agent_token
from agent.settings import AgentSettings
from agent.version import VERSION


class LocalSessionStore:
    def __init__(self, lifetime_seconds: int = 3600):
        self.lifetime_seconds = lifetime_seconds
        self._sessions: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        value = secrets.token_urlsafe(24)
        with self._lock:
            self._sessions[value] = time.time() + self.lifetime_seconds
        return value

    def valid(self, value: str | None) -> bool:
        if not value:
            return False
        now = time.time()
        with self._lock:
            expired = [key for key, expiry in self._sessions.items() if expiry <= now]
            for key in expired:
                self._sessions.pop(key, None)
            expiry = self._sessions.get(value)
            return bool(expiry and expiry > now)


def create_app(settings: AgentSettings | None = None, token: str | None = None) -> FastAPI:
    settings = (settings or AgentSettings()).validated()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    agent_token, token_path = (
        (token, settings.data_root / "agent_state.json")
        if token
        else load_or_create_agent_token(settings.data_root)
    )
    if not agent_token or len(agent_token) < 8:
        raise ValueError("agent token is invalid")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(title="Daily Business Agent", version=VERSION, lifespan=lifespan)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-Agent-Token"],
        )
    app.state.settings = settings
    app.state.agent_token = agent_token
    app.state.token_path = token_path
    app.state.sessions = LocalSessionStore()

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/lingxing", status_code=307)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": VERSION, "bind": f"{settings.host}:{settings.port}"}

    return app
