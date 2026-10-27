"""Thin async clients for Google (OAuth + Chat availability) and Spotify (OAuth + now playing)."""

from urllib.parse import urlencode

import httpx

from . import config

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHAT_SCOPE = "https://www.googleapis.com/auth/chat.users.availability"
CHAT_AVAILABILITY_URL = "https://chat.googleapis.com/v1/users/me/availability"

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SCOPE = "user-read-currently-playing"
SPOTIFY_NOW_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"

GOOGLE_REDIRECT = f"{config.BASE_URL}/auth/google/callback"
SPOTIFY_REDIRECT = f"{config.BASE_URL}/auth/spotify/callback"

http = httpx.AsyncClient(timeout=15)


class TokenRevoked(Exception):
    """The refresh token no longer works; the user has to reconnect."""


class Unauthorized(Exception):
    """The access token was rejected; refresh it and retry."""


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited for {retry_after:.0f}s")
        self.retry_after = retry_after


class Forbidden(Exception):
    """The API refused the request (e.g. user not allowlisted in a Spotify dev-mode app)."""


def _error_message(r: httpx.Response) -> str:
    try:
        error = r.json().get("error")
    except ValueError:
        return r.text[:200]
    if isinstance(error, dict):
        return error.get("message") or str(error)
    return str(error or r.text[:200])


def _raise_for(r: httpx.Response) -> None:
    if r.status_code == 401:
        raise Unauthorized(r.text)
    if r.status_code == 403:
        raise Forbidden(f"{r.url.host} refused access: {_error_message(r)}")
    if r.status_code == 429:
        raise RateLimited(float(r.headers.get("Retry-After", 30)))
    r.raise_for_status()


# --- Google -----------------------------------------------------------------


def google_auth_url(state: str) -> str:
    return GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT,
        "response_type": "code",
        "scope": f"openid email {CHAT_SCOPE}",
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh token
        "state": state,
    })


async def google_exchange_code(code: str) -> dict:
    r = await http.post(GOOGLE_TOKEN_URL, data={
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT,
        "grant_type": "authorization_code",
    })
    r.raise_for_status()
    return r.json()


async def google_userinfo(access_token: str) -> dict:
    r = await http.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    r.raise_for_status()
    return r.json()


async def google_refresh(refresh_token: str) -> tuple[str, int]:
    r = await http.post(GOOGLE_TOKEN_URL, data={
        "refresh_token": refresh_token,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    })
    if r.status_code in (400, 401) and "invalid_grant" in r.text:
        raise TokenRevoked("Google access was revoked or expired. Sign in again.")
    r.raise_for_status()
    data = r.json()
    return data["access_token"], int(data.get("expires_in", 3600))


async def google_revoke(token: str) -> None:
    await http.post(GOOGLE_REVOKE_URL, data={"token": token})


async def chat_get_status(access_token: str) -> dict | None:
    r = await http.get(CHAT_AVAILABILITY_URL, headers={"Authorization": f"Bearer {access_token}"})
    _raise_for(r)
    return r.json().get("customStatus")


async def chat_set_status(access_token: str, text: str) -> None:
    body = {"customStatus": {
        "text": text,
        "emoji": {"unicode": config.STATUS_EMOJI},
        "ttl": f"{config.STATUS_TTL}s",
    }}
    r = await http.patch(
        CHAT_AVAILABILITY_URL,
        params={"updateMask": "customStatus"},
        json=body,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    _raise_for(r)


async def chat_clear_status(access_token: str) -> None:
    r = await http.patch(
        CHAT_AVAILABILITY_URL,
        params={"updateMask": "customStatus"},
        json={},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    _raise_for(r)


# --- Spotify ----------------------------------------------------------------


def spotify_auth_url(state: str) -> str:
    return SPOTIFY_AUTH_URL + "?" + urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID,
        "redirect_uri": SPOTIFY_REDIRECT,
        "response_type": "code",
        "scope": SPOTIFY_SCOPE,
        "state": state,
    })


async def _spotify_token(data: dict) -> dict:
    r = await http.post(
        SPOTIFY_TOKEN_URL,
        data=data,
        auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
    )
    if r.status_code == 400 and "invalid_grant" in r.text:
        raise TokenRevoked("Spotify access was revoked. Reconnect Spotify.")
    r.raise_for_status()
    return r.json()


async def spotify_exchange_code(code: str) -> dict:
    return await _spotify_token({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT,
    })


async def spotify_refresh(refresh_token: str) -> tuple[str, int, str | None]:
    """Returns (access_token, expires_in, new_refresh_token_or_None)."""
    data = await _spotify_token({"grant_type": "refresh_token", "refresh_token": refresh_token})
    return data["access_token"], int(data.get("expires_in", 3600)), data.get("refresh_token")


async def spotify_now_playing(access_token: str) -> str | None:
    """'Track – Artist' (or 'Episode – Show') if something is playing, else None."""
    r = await http.get(
        SPOTIFY_NOW_PLAYING_URL,
        params={"additional_types": "track,episode"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if r.status_code == 204:
        return None
    _raise_for(r)
    data = r.json()
    item = data.get("item")
    if not data.get("is_playing") or not item:
        return None
    if item.get("type") == "episode":
        by = item.get("show", {}).get("name", "")
    else:
        by = ", ".join(a["name"] for a in item.get("artists", []))
    return f"{item['name']} – {by}" if by else item["name"]
