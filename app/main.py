"""Web app: sign in with Google, connect Spotify, manage the sync. The worker runs in the same process."""

import asyncio
import contextlib
import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, pages, providers, worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("now-playing")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    task = asyncio.create_task(worker.run_forever())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await providers.http.aclose()
    db.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="np_session",
    same_site="lax",  # blocks cross-site POSTs to the forms below
    https_only=config.BASE_URL.startswith("https://"),
    max_age=60 * 60 * 24 * 30,
)


def _current_user(request: Request) -> db.User | None:
    user_id = request.session.get("uid")
    user = db.get_user(user_id) if user_id else None
    if user_id and not user:
        request.session.clear()
    return user


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _error(title: str, text: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(pages.message(title, text), status_code=status)


def _check_state(request: Request, key: str, state: str | None) -> bool:
    expected = request.session.pop(key, None)
    return bool(expected and state and secrets.compare_digest(expected, state))


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = _current_user(request)
    return pages.dashboard(user) if user else pages.landing()


@app.get("/login")
async def login(request: Request):
    request.session["google_state"] = state = secrets.token_urlsafe(24)
    return RedirectResponse(providers.google_auth_url(state))


@app.get("/auth/google/callback")
async def google_callback(request: Request, code: str | None = None, state: str | None = None,
                          error: str | None = None):
    if error or not code:
        return _error("Sign-in cancelled", "Google sign-in didn't complete.")
    if not _check_state(request, "google_state", state):
        return _error("Sign-in expired", "Please try signing in again.")

    tokens = await providers.google_exchange_code(code)
    if providers.CHAT_SCOPE not in tokens.get("scope", "").split():
        return _error("Permission needed",
                      "Please allow access to your Google Chat status. It's the only thing we change.")
    info = await providers.google_userinfo(tokens["access_token"])
    email = info.get("email", "")
    if not config.is_allowed(email):
        return _error("Not available", f"{email} isn't on the list for this instance.", 403)

    request.session["uid"] = db.upsert_google_user(info["sub"], email, tokens.get("refresh_token"))
    return _redirect("/")


@app.get("/connect/spotify")
async def connect_spotify(request: Request):
    if not _current_user(request):
        return _redirect("/")
    request.session["spotify_state"] = state = secrets.token_urlsafe(24)
    return RedirectResponse(providers.spotify_auth_url(state))


@app.get("/auth/spotify/callback")
async def spotify_callback(request: Request, code: str | None = None, state: str | None = None,
                           error: str | None = None):
    user = _current_user(request)
    if not user:
        return _redirect("/")
    if error or not code:
        return _error("Spotify not connected", "Spotify authorisation didn't complete. "
                      "If this instance is in Spotify development mode, ask the owner to add you.")
    if not _check_state(request, "spotify_state", state):
        return _error("Link expired", "Please try connecting Spotify again.")

    tokens = await providers.spotify_exchange_code(code)
    db.set_spotify_token(user.id, tokens["refresh_token"])
    db.set_error(user.id, None)
    return _redirect("/")


@app.post("/toggle")
async def toggle(request: Request):
    if user := _current_user(request):
        db.set_enabled(user.id, not user.enabled)
        if user.enabled:
            await worker.clear_user(user)
    return _redirect("/")


@app.post("/disconnect/spotify")
async def disconnect_spotify(request: Request):
    if user := _current_user(request):
        await worker.clear_user(user)
        db.set_spotify_token(user.id, None)
    return _redirect("/")


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _redirect("/")


@app.post("/delete")
async def delete(request: Request):
    if user := _current_user(request):
        await worker.clear_user(user)
        if user.google_refresh_token:
            with contextlib.suppress(Exception):
                await providers.google_revoke(user.google_refresh_token)
        db.delete_user(user.id)
    request.session.clear()
    return _redirect("/")
