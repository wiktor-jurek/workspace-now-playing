"""Server-rendered HTML. Small enough that a template engine isn't worth it."""

from html import escape

from . import config
from .db import MAX_PREFIX, User

CSS = """
:root { --bg:#f7f7f5; --card:#fff; --fg:#1d1d1f; --muted:#6b6b70; --line:#e4e4e0;
        --accent:#1db954; --accent-fg:#fff; --danger:#c62828; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#121212; --card:#1c1c1e; --fg:#f2f2f2; --muted:#a0a0a6; --line:#2c2c2e; --danger:#ef5350; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:520px; margin:0 auto; padding:48px 16px; }
h1 { font-size:28px; margin:0 0 8px; }
p { color:var(--muted); margin:0 0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px; margin:16px 0; }
.row { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 0; }
.row + .row { border-top:1px solid var(--line); }
.label { font-weight:600; }
.sub { color:var(--muted); font-size:14px; }
.status { font-size:18px; padding:12px 14px; border-radius:10px; background:var(--bg); }
.btn { display:inline-block; border:0; border-radius:999px; padding:9px 18px; font:inherit; font-weight:600;
       cursor:pointer; text-decoration:none; background:var(--accent); color:var(--accent-fg); }
.btn.secondary { background:transparent; color:var(--fg); border:1px solid var(--line); }
.btn.danger { background:transparent; color:var(--danger); border:1px solid var(--line); }
form { margin:0; display:inline; }
.error { color:var(--danger); font-size:14px; margin-top:8px; }
.prefix-form { display:flex; gap:8px; margin-top:8px; }
.prefix-form input { flex:1; min-width:0; font:inherit; padding:8px 12px; border-radius:10px;
       border:1px solid var(--line); background:var(--bg); color:var(--fg); }
footer { color:var(--muted); font-size:13px; margin-top:32px; }
footer a { color:inherit; }
"""

_SOURCE = f' · <a href="{escape(config.SOURCE_URL)}">Source</a>' if config.SOURCE_URL else ""
FOOTER = f"""<footer>We store only your email and encrypted access tokens.
Delete your account at any time{_SOURCE}.</footer>"""


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body><main>{body}{FOOTER}</main></body></html>"""


def landing() -> str:
    return _layout("Now Playing for Google Chat", f"""
<h1>{config.STATUS_EMOJI} Now Playing</h1>
<p>Show what you're listening to on Spotify as your Google Chat status.
It updates when the track changes and clears when you pause.</p>
<div class="card">
  <a class="btn" href="/login">Sign in with Google</a>
</div>""")


def message(title: str, text: str) -> str:
    return _layout(title, f"""
<h1>{escape(title)}</h1>
<p>{escape(text)}</p>
<a class="btn secondary" href="/">Back</a>""")


def _post(action: str, label: str, cls: str = "secondary", confirm: str | None = None) -> str:
    onsubmit = f' onsubmit="return confirm(\'{escape(confirm)}\')"' if confirm else ""
    return f'<form method="post" action="{action}"{onsubmit}><button class="btn {cls}">{label}</button></form>'


def dashboard(user: User) -> str:
    google_ok = bool(user.google_refresh_token)
    spotify_ok = bool(user.spotify_refresh_token)

    if not (google_ok and spotify_ok):
        status = "Connect both accounts to start."
    elif not user.enabled:
        status = "Paused"
    elif user.last_status:
        status = f"{config.STATUS_EMOJI} {escape(user.last_status)}"
    else:
        status = "Nothing playing"

    error = f'<div class="error">{escape(user.last_error)}</div>' if user.last_error else ""

    google_action = (
        '<span class="sub">Connected</span>' if google_ok
        else '<a class="btn" href="/login">Reconnect</a>'
    )
    spotify_action = (
        _post("/disconnect/spotify", "Disconnect") if spotify_ok
        else '<a class="btn" href="/connect/spotify">Connect</a>'
    )
    toggle = _post("/toggle", "Pause" if user.enabled else "Resume", "secondary" if user.enabled else "")

    return _layout("Now Playing", f"""
<h1>{config.STATUS_EMOJI} Now Playing</h1>
<p>Signed in as {escape(user.email)}</p>
<div class="card">
  <div class="sub">Current Chat status</div>
  <div class="status">{status}</div>{error}
  <div class="row"><span></span>{toggle}</div>
</div>
<div class="card">
  <div class="label">Prefix</div>
  <div class="sub">Shown before the track, e.g. “{escape(user.prefix or 'Listening to')} Song – Artist”. Leave empty for none.</div>
  <form class="prefix-form" method="post" action="/prefix">
    <input name="prefix" value="{escape(user.prefix)}" maxlength="{MAX_PREFIX}" placeholder="Listening to" aria-label="Prefix">
    <button class="btn secondary">Save</button>
  </form>
</div>
<div class="card">
  <div class="row"><div><div class="label">Google Chat</div><div class="sub">Sets your custom status</div></div>{google_action}</div>
  <div class="row"><div><div class="label">Spotify</div><div class="sub">Reads what's playing</div></div>{spotify_action}</div>
</div>
<div class="card">
  <div class="row">{_post("/logout", "Sign out")}
  {_post("/delete", "Delete account", "danger", "Delete your account and revoke access?")}</div>
</div>""")
