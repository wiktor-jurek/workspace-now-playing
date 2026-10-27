# 🎵 Now Playing for Google Chat

Show what you're listening to on Spotify as your **Google Chat status**, the same way Slack and Discord can.

```
🎵 Waking the Demon – Bullet For My Valentine
```

Sign in with Google, connect Spotify, and you're done. The status follows your track changes, clears when you pause, and never overwrites a status you set yourself.

It's a small self-hostable service: a single Docker container with SQLite and no other infrastructure. Deploy it on [Coolify](https://coolify.io) or any Docker host.

## How it works

```
 Spotify Web API ──(every 15s)──▶  worker  ──(only on change)──▶  Google Chat API
 /me/player/currently-playing                                       users.availability.patch
```

- **Google:** uses the official [`users.availability`](https://developers.google.com/workspace/chat/api/reference/rest/v1/users.availability) endpoint of the Google Chat API, with the `chat.users.availability` scope. That scope lets the app read and set your status and nothing else: no messages, no spaces.
- **Spotify:** uses [Get Currently Playing Track](https://developer.spotify.com/documentation/web-api/reference/get-the-users-currently-playing-track) with the `user-read-currently-playing` scope. Playback on your phone, desktop, or a speaker all counts. Podcasts show as `Episode – Show`.
- **Status lifetime:** each status is set with a 10-minute TTL and refreshed while the track keeps playing. If the service goes down, statuses clear themselves.
- **Manual statuses are left alone:** if your current status uses any emoji other than 🎵, the service won't touch it.
- **Storage:** only your email and the two refresh tokens, encrypted at rest. *Delete account* clears your status, revokes Google access, and deletes the row.

## Self-hosting

You need three things: a Google Cloud OAuth client, a Spotify app, and somewhere to run a container. The examples use [Coolify](https://coolify.io), but any Docker host works.

### 1. Google Cloud

1. Create or pick a project in the [Google Cloud console](https://console.cloud.google.com/).
2. **Enable the Google Chat API** (APIs & Services → Library).
3. **Configure the Chat app.** Go to APIs & Services → Google Chat API → **Configuration**, then:
   - App name: `Now Playing`, plus any avatar URL and a description.
   - Turn **Interactive features off**. If the form insists on a connection, set HTTP endpoint URL to your `BASE_URL`; it never gets called.
   - Save.

   > ⚠️ This step is easy to miss. Without it, every status update fails with `404 Google Chat app not found`, even though the API is enabled.

4. **OAuth consent screen** (Google Auth Platform):
   - User type: *Internal* if only people in your Workspace will use it; otherwise *External*.
   - Scopes: `openid`, `email`, `https://www.googleapis.com/auth/chat.users.availability`.
   - With *External* in **Testing** mode, add each user as a test user. Their refresh tokens **expire after 7 days**, so publish the app ("In production") for anything long-lived. Unverified apps show a warning screen and are capped at 100 users.
5. **Credentials** → Create OAuth client ID → **Web application**:
   - Authorised redirect URI: `https://YOUR-DOMAIN/auth/google/callback`
   - Copy the client ID and secret.

### 2. Spotify

1. Create an app at the [Spotify developer dashboard](https://developer.spotify.com/dashboard) with a Spotify account that has **Premium**. Spotify blocks development-mode apps whose owner isn't on Premium. Choose **Web API**.
2. Redirect URI: `https://YOUR-DOMAIN/auth/spotify/callback`
3. Copy the client ID and secret.
4. New Spotify apps run in **development mode**: only users you add under *User Management* can connect, up to a small cap. Fine for a team; a public instance needs Spotify's extended quota.

### 3. Deploy on Coolify

1. New resource → **Public/Private Git repository** → this repo. Build pack: **Docker Compose** (uses `docker-compose.yml`).
2. Set the domain for the `app` service, e.g. `https://now-playing.example.com`. Coolify's proxy handles HTTPS; the container listens on port 8000.
3. Add the environment variables below. Coolify picks them up from the compose file.
4. Deploy. The SQLite database lives in the `data` volume, so it survives redeploys.

Using another host? `docker compose up -d` with a `.env` file (see `.env.example`) behind any HTTPS reverse proxy works the same way.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `BASE_URL` | ✅ | Public URL, e.g. `https://now-playing.example.com`. OAuth redirect URIs are derived from it. |
| `SECRET_KEY` | ✅ | Signs session cookies and encrypts stored tokens. Generate with `openssl rand -hex 32`. Changing it invalidates all stored tokens. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | ✅ | Google OAuth web client. |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | ✅ | Spotify app credentials. |
| `ALLOWED_EMAILS` | | Comma-separated emails allowed to sign in. |
| `ALLOWED_DOMAINS` | | Comma-separated email domains allowed to sign in. If both are empty, anyone can sign in. |
| `POLL_INTERVAL` | | Seconds between Spotify checks (default `15`). |
| `STATUS_EMOJI` | | Emoji used for the status (default `🎵`). |
| `STATUS_TTL` | | Status lifetime in seconds (default `600`). |
| `CONCURRENCY` | | Users synced in parallel (default `10`). |
| `SOURCE_URL` | | Link to the source code, shown in the footer. |

Run a **single replica**: the sync worker runs inside the web process.

## Troubleshooting

| Error | Fix |
|---|---|
| `404 Google Chat app not found` | Configure the Chat app (Google Cloud step 3). |
| `api.spotify.com refused access` (403) | The Spotify app is in development mode and this Spotify account isn't on its list. Add the user's **Spotify** email under *User Management* in the Spotify dashboard, then disconnect and reconnect Spotify. |
| `Active premium subscription required for the owner of the app` | The Spotify account that **owns the developer app** needs Premium. Upgrade it, or recreate the app under a Premium account and update `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET`. After a subscription change, Spotify can take a few hours to allow requests. |
| `no available server` | Coolify's proxy has no healthy container. Usually this is a redeploy in progress; otherwise check the container logs in Coolify. |
| Users signed out of Google after 7 days | The OAuth consent screen is *External* and still in *Testing*. Publish it. |

## Local development

```sh
cp .env.example .env   # fill in; BASE_URL=http://127.0.0.1:8000
uv sync
set -a; source .env; set +a
uv run uvicorn app.main:app --reload
```

Spotify doesn't accept `localhost` redirect URIs; use `http://127.0.0.1:8000/auth/spotify/callback`.

## Project layout

```
app/
  main.py       routes: Google sign-in, Spotify connect, dashboard actions
  worker.py     background sync loop
  providers.py  Google + Spotify HTTP clients
  db.py         SQLite + token encryption
  pages.py      HTML
  config.py     environment variables
Dockerfile
docker-compose.yml
```

## License

MIT
