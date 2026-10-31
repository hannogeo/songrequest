#!/usr/bin/env python3
"""Twitch song request bot for Spotify.

Run with:  python app.py
First run launches a setup wizard that stores config in ~/.songrequest/config.json.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".songrequest"
CONFIG_FILE = CONFIG_DIR / "config.json"

REQUIRED_KEYS = [
    "twitch_bot_username",
    "twitch_client_id",
    "twitch_oauth_token",
    "twitch_channel",
    "spotify_client_id",
    "spotify_client_secret",
    "spotify_redirect_uri",
]

OPTIONAL_KEYS = ["groq_api_key"]


def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_config() -> dict[str, Any] | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[config] Failed to read config: {e}")
        return None


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"[config] Saved to {CONFIG_FILE}")


def twitch_device_login(client_id: str) -> tuple[str, str]:
    import time
    import webbrowser
    import requests

    resp = requests.post(
        "https://id.twitch.tv/oauth2/device",
        data={"client_id": client_id, "scopes": "chat:read chat:edit"},
        timeout=15,
    )
    data = resp.json()
    if "device_code" not in data:
        raise RuntimeError(f"Twitch device login failed: {data}")
    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri_complete") or data.get("verification_uri")
    interval = int(data.get("interval", 5))
    expires_in = int(data.get("expires_in", 1800))

    print(f"\nOpen this URL in your browser: {verification_uri}")
    print(f"And enter code: {user_code}\n")
    try:
        webbrowser.open(verification_uri)
    except Exception:
        pass

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            timeout=15,
        )
        j = r.json()
        if "access_token" in j:
            return j["access_token"], j.get("refresh_token", "")
        err = j.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err in ("expired_token", "access_denied"):
            raise RuntimeError(f"Twitch login {err}")
        # unknown
        print(f"[twitch] {j}")
    raise RuntimeError("Twitch device code expired")


def fetch_twitch_login(client_id: str, access_token: str) -> str:
    import requests

    try:
        r = requests.get(
            "https://api.twitch.tv/helix/users",
            headers={"Authorization": f"Bearer {access_token}", "Client-Id": client_id},
            timeout=15,
        )
        j = r.json()
        users = j.get("data") or []
        if users:
            return users[0].get("login", "")
    except Exception as e:
        print(f"[twitch] could not fetch login: {e}")
    return ""


def run_setup() -> dict[str, Any]:
    print("=" * 60)
    print("  Twitch Song Request - First-time setup")
    print("=" * 60)
    print("Press Enter to accept defaults where shown.\n")

    cfg: dict[str, Any] = {}

    def ask(key: str, prompt: str, secret: bool = False, default: str = "") -> str:
        while True:
            disp = f"{prompt}"
            if default:
                disp += f" [{default}]"
            disp += ": "
            val = input(disp).strip()
            if not val and default:
                val = default
            if val:
                return val
            print("  This field is required.")

    print("\nTwitch setup:")
    print("  1. Go to https://dev.twitch.tv/console and create an application.")
    print("  2. Set the OAuth Redirect URL to anything (e.g. http://localhost) - it is not used.")
    print("  3. Copy the Client ID below.")
    cfg["twitch_client_id"] = ask("twitch_client_id", "Twitch Client ID")

    print("\nLogging in to Twitch via device code...")
    access, refresh = twitch_device_login(cfg["twitch_client_id"])
    cfg["twitch_oauth_token"] = access
    cfg["twitch_refresh_token"] = refresh

    # fetch bot username from token
    login = fetch_twitch_login(cfg["twitch_client_id"], access)
    if not login:
        login = ask("twitch_bot_username", "Twitch bot username (could not auto-detect)")
    cfg["twitch_bot_username"] = login
    print(f"[twitch] Logged in as {login}")

    ch = ask("twitch_channel", "Twitch channel to watch (without #)")
    cfg["twitch_channel"] = ch.lstrip("#").lower()

    print("\nSpotify setup:")
    print("  1. Go to https://developer.spotify.com/dashboard and create an app.")
    print("  2. Add http://127.0.0.1:8888/callback as a Redirect URI.")
    print("  3. Copy the Client ID and Client Secret below.")
    cfg["spotify_client_id"] = ask("spotify_client_id", "Spotify Client ID")
    cfg["spotify_client_secret"] = ask("spotify_client_secret", "Spotify Client Secret")
    cfg["spotify_redirect_uri"] = "http://127.0.0.1:8888/callback"

    groq = input("Groq API key (optional, for GPT-OSS double-check; leave blank to skip): ").strip()
    cfg["groq_api_key"] = groq

    # optional tuning
    cfg["fuzzy_threshold"] = 0.30
    cfg["cooldown_seconds"] = 5

    save_config(cfg)
    return cfg


# ---------- Spotify ----------

_spotify = None


def get_spotify(cfg):
    global _spotify
    if _spotify is not None:
        return _spotify
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    cache_path = str(CONFIG_DIR / "spotify_cache")
    auth = SpotifyOAuth(
        client_id=cfg["spotify_client_id"],
        client_secret=cfg["spotify_client_secret"],
        redirect_uri=cfg["spotify_redirect_uri"],
        scope="user-modify-playback-state user-read-playback-state",
        cache_path=cache_path,
        open_browser=True,
    )
    sp = spotipy.Spotify(auth_manager=auth)
    # touch to trigger auth
    try:
        me = sp.current_user()
        print(f"[spotify] Logged in as {me.get('display_name') or me.get('id')}")
    except Exception as e:
        print(f"[spotify] Auth check failed: {e}")
    _spotify = sp
    return sp


def spotify_search(sp, query: str, limit: int = 10) -> list[dict]:
    try:
        res = sp.search(q=query, type="track", limit=limit)
    except Exception as e:
        print(f"[spotify] search error: {e}")
        return []
    items = res.get("tracks", {}).get("items", []) or []
    out = []
    for t in items:
        artists = ", ".join(a.get("name", "") for a in (t.get("artists") or []))
        out.append({
            "id": t.get("id"),
            "uri": t.get("uri"),
            "name": t.get("name"),
            "artist": artists,
            "album": (t.get("album") or {}).get("name"),
        })
    return out


def score_track(query: str, track: dict) -> float:
    q = normalize(query)
    title = normalize(track.get("name", ""))
    artist = normalize(track.get("artist", ""))
    full = f"{artist} {title}".strip()
    scores = [
        difflib.SequenceMatcher(None, q, full).ratio(),
        difflib.SequenceMatcher(None, q, title).ratio(),
    ]
    # bonus if all query tokens appear
    qtokens = set(q.split())
    ftokens = set(full.split())
    if qtokens and qtokens.issubset(ftokens):
        scores.append(0.95)
    # token overlap jaccard
    if qtokens and ftokens:
        inter = len(qtokens & ftokens)
        union = len(qtokens | ftokens)
        scores.append(inter / union)
    return max(scores) if scores else 0.0


def pick_best(query: str, candidates: list[dict], groq_key: str | None) -> tuple[dict | None, str]:
    """Return (chosen track or None, reason)."""
    if not candidates:
        return None, "no candidates"

    # fuzzy rank
    ranked = sorted(candidates, key=lambda t: score_track(query, t), reverse=True)
    best = ranked[0]
    best_score = score_track(query, best)
    threshold = 0.30

    # Groq double-check if available
    if groq_key:
        try:
            from groq import Groq

            models_to_try = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
            client = Groq(api_key=groq_key)
            cand_lines = []
            for i, t in enumerate(ranked[:8], start=1):
                cand_lines.append(f"{i}. {t['artist']} - {t['name']}")
            prompt = (
                "A user requested a song on Twitch chat with this text:\n"
                f"\"{query}\"\n\n"
                "Pick the best matching track from the list below. "
                "Consider typos and partial names. Reply with ONLY the number of the best match, "
                "or 0 if none of them fit.\n\n" + "\n".join(cand_lines)
            )
            chosen_idx = None
            for model in models_to_try:
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0,
                    )
                    txt = (resp.choices[0].message.content or "").strip()
                    m = re.search(r"\d+", txt)
                    if m:
                        chosen_idx = int(m.group())
                        break
                except Exception as e:
                    print(f"[groq] model {model} failed: {e}")
                    continue
            if chosen_idx is not None:
                if chosen_idx == 0:
                    return None, "groq: no match"
                if 1 <= chosen_idx <= len(ranked[:8]):
                    return ranked[chosen_idx - 1], f"groq picked #{chosen_idx}"
        except Exception as e:
            print(f"[groq] error: {e}")

    if best_score >= threshold:
        return best, f"fuzzy score {best_score:.2f}"
    # low confidence: still suggest best but don't queue
    return None, f"low confidence ({best_score:.2f}); best was {best['artist']} - {best['name']}"


def queue_track(sp, track: dict) -> tuple[bool, str]:
    try:
        devices = sp.devices().get("devices", []) or []
        device_id = None
        for d in devices:
            if d.get("is_active"):
                device_id = d.get("id")
                break
        if device_id is None and devices:
            device_id = devices[0].get("id")
        sp.add_to_queue(track["uri"], device_id=device_id)
        return True, "queued"
    except Exception as e:
        return False, str(e)


def refresh_twitch_token(cfg) -> str:
    """Refresh Twitch access token if refresh credentials are present."""
    cid = (cfg.get("twitch_client_id") or "").strip()
    rtoken = (cfg.get("twitch_refresh_token") or "").strip()
    if not (cid and rtoken):
        return cfg.get("twitch_oauth_token", "")
    try:
        import requests
        resp = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": cid,
                "grant_type": "refresh_token",
                "refresh_token": rtoken,
            },
            timeout=15,
        )
        data = resp.json()
        if "access_token" in data:
            cfg["twitch_oauth_token"] = data["access_token"]
            if "refresh_token" in data:
                cfg["twitch_refresh_token"] = data["refresh_token"]
            try:
                save_config(cfg)
            except Exception:
                pass
            print("[twitch] Access token refreshed.")
            return cfg["twitch_oauth_token"]
        else:
            print(f"[twitch] Token refresh failed: {data}")
    except Exception as e:
        print(f"[twitch] Token refresh error: {e}")
    return cfg.get("twitch_oauth_token", "")


# ---------- Twitch IRC ----------

class TwitchChat:
    def __init__(self, cfg, on_message):
        self.cfg = cfg
        self.on_message = on_message
        self.reader = None
        self.writer = None

    async def connect(self):
        # Try TLS first, fall back to plain IRC.
        for port, use_ssl in ((6697, True), (6667, False)):
            try:
                if use_ssl:
                    ctx = ssl.create_default_context()
                    self.reader, self.writer = await asyncio.open_connection(
                        "irc.chat.twitch.tv", port, ssl=ctx
                    )
                else:
                    self.reader, self.writer = await asyncio.open_connection(
                        "irc.chat.twitch.tv", port
                    )
                print(f"[twitch] Connected via port {port} (ssl={use_ssl})")
                break
            except Exception as e:
                print(f"[twitch] port {port} failed: {e}")
                self.reader = self.writer = None
        if self.reader is None:
            raise RuntimeError("Could not connect to Twitch IRC")
        token = self.cfg["twitch_oauth_token"]
        nick = self.cfg["twitch_bot_username"]
        channel = self.cfg["twitch_channel"]
        self.writer.write(f"PASS oauth:{token}\r\n".encode())
        self.writer.write(f"NICK {nick}\r\n".encode())
        self.writer.write(f"JOIN #{channel}\r\n".encode())
        await self.writer.drain()
        print(f"[twitch] Connected as {nick}, joined #{channel}")

    async def send(self, text: str):
        channel = self.cfg["twitch_channel"]
        try:
            self.writer.write(f"PRIVMSG #{channel} :{text}\r\n".encode())
            await self.writer.drain()
        except Exception as e:
            print(f"[twitch] send error: {e}")

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(refresh_twitch_token, self.cfg)
                await self.connect()
            except Exception as e:
                print(f"[twitch] connect failed: {e}")
                await asyncio.sleep(5)
                continue
            # read loop
            while True:
                try:
                    data = await self.reader.readline()
                except Exception as e:
                    print(f"[twitch] read error: {e}")
                    break
                if not data:
                    print("[twitch] connection closed")
                    break
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    continue
                # PING
                if line.startswith("PING"):
                    try:
                        self.writer.write(b"PONG :tmi.twitch.tv\r\n")
                        await self.writer.drain()
                    except Exception:
                        break
                    continue
                # parse PRIVMSG
                m = re.search(r":(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)", line)
                if m:
                    user = m.group(1)
                    text = m.group(2)
                    try:
                        await self.on_message(user, text, self)
                    except Exception as e:
                        print(f"[handler] error: {e}")
                # log other notices
                if "NOTICE" in line or "001" in line or "JOIN" in line:
                    print(f"[twitch] {line}")
            # reconnect
            try:
                if self.writer:
                    self.writer.close()
                    await self.writer.wait_closed()
            except Exception:
                pass
            self.reader = self.writer = None
            print("[twitch] Reconnecting in 5s...")
            await asyncio.sleep(5)


# ---------- Main handler ----------

def make_handler(cfg):
    sp = get_spotify(cfg)
    groq_key = cfg.get("groq_api_key") or None
    cooldowns: dict[str, float] = {}
    cooldown = cfg.get("cooldown_seconds", 5)

    async def handler(user: str, text: str, chat: TwitchChat):
        text = text.strip()
        m = re.match(r"^!(?:sr|songrequest|request)\s+(.+)$", text, re.IGNORECASE)
        if not m:
            return
        query = m.group(1).strip()
        if not query:
            await chat.send(f"@{user} usage: !sr <song name>")
            return
        now = time.time()
        if user in cooldowns and now - cooldowns[user] < cooldown:
            return
        cooldowns[user] = now

        print(f"[request] {user}: {query}")

        candidates = spotify_search(sp, query, limit=10)
        if not candidates:
            await chat.send(f"@{user} no results found on Spotify.")
            return

        chosen, reason = pick_best(query, candidates, groq_key)
        if chosen is None:
            # suggest top fuzzy
            ranked = sorted(candidates, key=lambda t: score_track(query, t), reverse=True)[:3]
            sugg = "; ".join(f"{t['artist']} - {t['name']}" for t in ranked)
            await chat.send(f"@{user} couldn't confirm a match. Suggestions: {sugg}")
            print(f"[no-match] {user}: {reason} | {sugg}")
            return

        ok, msg = queue_track(sp, chosen)
        if ok:
            await chat.send(f"@{user} queued: {chosen['artist']} - {chosen['name']}")
            print(f"[queued] {chosen['artist']} - {chosen['name']} for {user} ({reason})")
        else:
            await chat.send(f"@{user} failed to queue: {msg}")
            print(f"[queue-error] {msg}")

    return handler


async def main():
    if "--setup" in sys.argv or "-s" in sys.argv:
        cfg = run_setup()
    else:
        cfg = load_config()
        if cfg is None or not all(cfg.get(k) for k in REQUIRED_KEYS) or not (cfg.get("twitch_oauth_token") or cfg.get("twitch_refresh_token")):
            cfg = run_setup()

    # ensure config dir
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    handler = make_handler(cfg)
    chat = TwitchChat(cfg, handler)

    print("[app] Starting. Press Ctrl+C to stop.")
    try:
        await chat.run()
    except KeyboardInterrupt:
        print("\n[app] Stopping.")
    finally:
        try:
            if chat.writer:
                chat.writer.close()
                await chat.writer.wait_closed()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
