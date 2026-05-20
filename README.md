# tcdd-telegram

Interactive Telegram bot for TCDD train ticket search + alarms.

## What it does

- `/search` — Nereden / Nereye / Tarih / Yolcu sayısı seçtir, boş koltuklu trenleri listele, TCDD'ye deeplink.
- `/alarm` — aynı parametreleri al, alarm kur. ±1 gün esnek. Boş yer çıkınca uyarır.
- `/alarms`, `/clear`, `/pause`, `/resume` — alarm yönetimi.
- Tekerlekli sandalye koltukları sayımdan çıkarılır.
- Kullanıcı başına en fazla 5 aktif alarm, saatte 10 arama.

## Architecture

- **Bot** (`src/tcdd_bot/main.py`) — `python-telegram-bot` long-polling on Fly.io.
- **Checker** (`scripts/check_alarms.py`) — GitHub Actions, every 30 min + 0–15 min jitter.
- **State** — Upstash Redis (shared between bot and checker).
- **TCDD client** — `src/tcdd_bot/tcdd.py`. Two backends:
  - `LiveBackend` (default): real TCDD JSON API at `web-api-prod-ytp.tcddtasimacilik.gov.tr/tms`. Uses `curl_cffi` with Chrome ja3 impersonation because TCDD's edge ja3-fingerprints non-browser clients.
  - `StubBackend`: deterministic fake trains for local development. Set `TCDD_MODE=stub` to use.

## Setup

### 1. Telegram bot

Create one via @BotFather, copy the token.

### 2. Upstash Redis

Create a free database at upstash.com, copy the REST URL + token.

### 3. Local dev

```bash
cd ~/Projects/tcdd-telegram
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill in BOT_TOKEN, UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
python -m tcdd_bot.main
```

In Telegram, message your bot: `/start`, then `/search`.

Run the checker locally:

```bash
SKIP_JITTER=1 python scripts/check_alarms.py
```

### 4. Deploy bot to Fly.io

```bash
flyctl launch --no-deploy
flyctl secrets set \
  BOT_TOKEN=… \
  UPSTASH_REDIS_REST_URL=… \
  UPSTASH_REDIS_REST_TOKEN=…
flyctl deploy
```

### 5. Wire up GitHub Actions checker

```bash
gh repo create tcdd-telegram --private --source=. --remote=origin --push
gh secret set BOT_TOKEN
gh secret set UPSTASH_REDIS_REST_URL
gh secret set UPSTASH_REDIS_REST_TOKEN
# Optional once live API works:
# gh secret set TCDD_BEARER_TOKEN
# gh variable set TCDD_MODE --body live
```

Trigger once to test:

```bash
gh workflow run check-alarms.yml
```

## Notes

- **WAF**: TCDD's edge ja3-fingerprints non-browser clients. We use `curl_cffi`
  with `impersonate="chrome120"` which mimics Chrome's TLS stack exactly.
  Standard `httpx` / `requests` get 403.
- **Bearer token**: the production JS bundle embeds a JWT whose `exp` is in
  2024 — but the TCDD gateway doesn't validate it. We hardcode the same token
  in [tcdd.py](src/tcdd_bot/tcdd.py). If TCDD ever rotates it, re-extract from
  the production JS (`case"TCDD-PROD":F="..."`).

## Files

```
src/tcdd_bot/
  main.py              bot entrypoint, long-polling
  config.py            env loading
  tcdd.py              TCDD search client (StubBackend, LiveBackend)
  stations.py          station catalog from CDN + fuzzy match
  store.py             Upstash Redis-backed user/alarm/rate-limit store
  format.py            message rendering
  handlers/
    start.py           /start, /help
    search.py          /search conversation
    alarm.py           /alarm, /alarms, /clear, /pause, /resume
    common.py          shared inline keyboards
scripts/
  check_alarms.py      periodic checker (GitHub Actions entrypoint)
.github/workflows/
  check-alarms.yml     cron */30 + workflow_dispatch
Dockerfile             Fly.io image
fly.toml               Fly.io app config
```
