# Twitch Song Request (Spotify)

A small command-line bot that watches a Twitch channel's chat for `!sr <song>` messages
and queues the matched track on your Spotify account.

## Features

- First-run interactive setup wizard (Twitch + Spotify + optional Groq).
- Fuzzy matching to tolerate typos in song requests.
- Optional Groq (GPT-OSS) double-check to pick the right track.
- Replies in Twitch chat with the queued track (or suggestions when unsure).

## Requirements

- Python 3.10+
- A Twitch application (Client ID; no OAuth token needed - login is device-code).
- A Spotify application (Client ID + Secret).
- (Optional) A Groq API key for GPT-OSS double-checking.

## Install dependencies

```bash
pip install spotipy groq
```

(`twitchio` is not required; chat is read over Twitch IRC.)

## Setup

### 1. Create a Twitch application
1. Go to https://dev.twitch.tv/console and log in.
2. Click **Register Your Application**.
3. Give it a name, set **OAuth Redirect URLs** to `http://localhost` (required by the form but not actually used), and select a category.
4. Open the application and copy the **Client ID**.

### 2. Create a Spotify application
1. Go to https://developer.spotify.com/dashboard and log in.
2. Click **Create app**.
3. Add `http://127.0.0.1:8888/callback` as a **Redirect URI**.
4. Copy the **Client ID** and **Client Secret**.

### 3. Run the setup wizard
```bash
python app.py
# or: sr
```
On first run you will be asked for:
- Twitch Client ID
- Twitch channel to watch
- Spotify Client ID, Client Secret
- (Optional) Groq API key

Then a browser opens to Twitch's activation page with a short code. Enter the code to log the bot in.
Spotify also opens a browser once for authorization.

Config is stored in `~/.songrequest/config.json`. Run `python app.py --setup` to re-run the wizard.

## Usage

```bash
python app.py        # or: sr
python app.py --setup # re-run the setup wizard
```

In Twitch chat:

```
!sr never gonna give you up
!sr bohemian rhapsody queen
```

## Notes

- Spotify needs an active device (open the Spotify app on your phone/PC) for queueing to work.
- No Redirect URL is needed for Twitch (device-code login is used); the form just requires one, so use `http://localhost`.
- Twitch access tokens expire; the bot refreshes them automatically using the refresh token from login.
- Groq is optional; without it, fuzzy matching is used.
- Ctrl+C stops the bot.
