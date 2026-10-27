"""Background loop: for each connected user, mirror Spotify's now playing into their Chat status."""

import asyncio
import logging
import time
from dataclasses import dataclass

from . import config, db, providers
from .providers import Forbidden, RateLimited, TokenRevoked, Unauthorized

log = logging.getLogger("now-playing.worker")

MAX_TEXT = 64  # Chat's limit for custom status text
REFRESH_SECONDS = config.STATUS_TTL / 2  # re-send an unchanged track before its TTL runs out
_UNKNOWN = "\0unknown"  # forces a sync on the first round after (re)start


@dataclass
class _State:
    google_access: str | None = None
    google_expiry: float = 0
    spotify_access: str | None = None
    spotify_expiry: float = 0
    last: str | None = _UNKNOWN
    last_sent: float = 0


_states: dict[int, _State] = {}
_paused_until = 0.0  # set when Spotify rate-limits the whole app


async def _google_token(user: db.User, st: _State) -> str:
    if st.google_access and time.monotonic() < st.google_expiry - 60:
        return st.google_access
    try:
        st.google_access, expires_in = await providers.google_refresh(user.google_refresh_token)
    except TokenRevoked:
        db.set_google_token(user.id, None)
        raise
    st.google_expiry = time.monotonic() + expires_in
    return st.google_access


async def _spotify_token(user: db.User, st: _State) -> str:
    if st.spotify_access and time.monotonic() < st.spotify_expiry - 60:
        return st.spotify_access
    try:
        st.spotify_access, expires_in, new_refresh = await providers.spotify_refresh(user.spotify_refresh_token)
    except TokenRevoked:
        db.set_spotify_token(user.id, None)
        raise
    if new_refresh and new_refresh != user.spotify_refresh_token:
        db.set_spotify_token(user.id, new_refresh)
        user.spotify_refresh_token = new_refresh
    st.spotify_expiry = time.monotonic() + expires_in
    return st.spotify_access


async def _now_playing(user: db.User, st: _State) -> str | None:
    try:
        return await providers.spotify_now_playing(await _spotify_token(user, st))
    except Unauthorized:
        st.spotify_access = None
        return await providers.spotify_now_playing(await _spotify_token(user, st))


def status_text(prefix: str, track: str) -> str:
    text = f"{prefix} {track}" if prefix else track
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _is_ours(status: dict | None) -> bool:
    return bool(status) and status.get("emoji", {}).get("unicode") == config.STATUS_EMOJI


async def sync_user(user: db.User) -> None:
    st = _states.setdefault(user.id, _State())
    track = await _now_playing(user, st)
    text = status_text(user.prefix, track) if track else None
    stale = time.monotonic() - st.last_sent > REFRESH_SECONDS
    if text == st.last and not (text and stale):
        return

    try:
        token = await _google_token(user, st)
        current = await providers.chat_get_status(token)
        if current and not _is_ours(current):
            db.set_error(user.id, "You have your own status set, so it's left alone.")
        elif text:
            await providers.chat_set_status(token, text)
            log.info("user %s: %s", user.id, text)
            db.set_status(user.id, text)
        else:
            if current:
                await providers.chat_clear_status(token)
                log.info("user %s: cleared", user.id)
            db.set_status(user.id, None)
    except Unauthorized:
        st.google_access = None
        raise
    st.last, st.last_sent = text, time.monotonic()


async def clear_user(user: db.User) -> None:
    """Remove our status (if it's still ours) and forget cached tokens. Used on disable/disconnect/delete."""
    st = _states.pop(user.id, None) or _State()
    if not user.google_refresh_token:
        return
    try:
        token = await _google_token(user, st)
        if _is_ours(await providers.chat_get_status(token)):
            await providers.chat_clear_status(token)
    except Exception as e:
        log.warning("user %s: could not clear status: %s", user.id, e)
    db.set_status(user.id, None)


async def _sync_guarded(user: db.User, sem: asyncio.Semaphore) -> None:
    global _paused_until
    async with sem:
        try:
            await sync_user(user)
        except TokenRevoked as e:
            _states.pop(user.id, None)
            db.set_error(user.id, str(e))
        except Forbidden as e:
            log.warning("user %s: %s", user.id, e)
            db.set_error(user.id, str(e))
        except RateLimited as e:
            _paused_until = time.monotonic() + e.retry_after
            log.warning("rate limited, pausing %.0fs", e.retry_after)
        except Exception as e:
            log.warning("user %s: %s", user.id, e)
            db.set_error(user.id, f"Temporary error: {e}")


async def run_forever() -> None:
    sem = asyncio.Semaphore(config.CONCURRENCY)
    while True:
        started = time.monotonic()
        if started >= _paused_until:
            users = db.active_users()
            for stale_id in _states.keys() - {u.id for u in users}:
                del _states[stale_id]
            await asyncio.gather(*(_sync_guarded(u, sem) for u in users))
        await asyncio.sleep(max(1.0, config.POLL_INTERVAL - (time.monotonic() - started)))
