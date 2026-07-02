# tcdd-telegram

Interactive Telegram bot for TCDD train ticket search + alarms.

## What it does

- `/search` — Nereden / Nereye / Tarih(ler) / Yolcu sayısı seçtir, boş koltuklu trenleri listele, TCDD'ye deeplink. Birden fazla gün seçilebilir.
- `/alarm` — aynı parametreleri al, alarm kur. Birden fazla gün seçilebilir; yalnızca seçilen günler için kontrol edilir. Boş yer çıkınca uyarır.
- `/alarms`, `/clear`, `/pause`, `/resume` — alarm yönetimi.
- Tekerlekli sandalye koltukları sayımdan çıkarılır.
- Kullanıcı başına en fazla 5 aktif alarm, saatte 10 arama.

## Architecture

- **Bot** (`src/tcdd_bot/main.py`) — `python-telegram-bot` long-polling on Fly.io.
- **Checker** (`src/tcdd_bot/checker.py`) — runs inside the bot process via PTB's
  `JobQueue`, every `CHECK_INTERVAL_MIN` minutes (default 10) with a random
  initial jitter. Same code is also callable as a one-off via
  `scripts/check_alarms.py`.
- **State** — Fly.io managed Upstash Redis (Pay-as-you-go, native protocol).
- **TCDD client** — `src/tcdd_bot/tcdd.py`. Two backends:
  - `LiveBackend` (default): real TCDD JSON API at `web-api-prod-ytp.tcddtasimacilik.gov.tr/tms`. Uses `curl_cffi` with Chrome ja3 impersonation because TCDD's edge ja3-fingerprints non-browser clients.
  - `StubBackend`: deterministic fake trains for local development. Set `TCDD_MODE=stub` to use.

## Setup

### 1. Telegram bot

Create one via @BotFather, copy the token.

### 2. Redis

We use Fly.io's managed Upstash Redis (`fly redis create --plan Pay-as-you-go`),
which is effectively free for personal use ($0.20 per 100K commands).
Native Redis protocol — copy the `redis://default:PASSWORD@HOST:6379` URL.

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

### Tests

```bash
pip install -e '.[dev]'
pytest
```

Unit tests (no network / no Redis — uses `fakeredis` and a stub backend) cover
config parsing, station fuzzy-match, TCDD response parsing, message rendering,
the Redis store, the alarm checker, and the access gate.

### 4. Deploy bot to Fly.io

```bash
flyctl launch --no-deploy
flyctl secrets set \
  BOT_TOKEN=… \
  REDIS_URL=redis://default:…@fly-tcdd-redis.upstash.io:6379
flyctl deploy
```

### 5. Periodic checker

The checker runs automatically inside the bot process. No extra setup. See
`fly logs` for "checker scheduled" / "checker: N active alarms" lines.

To run it ad-hoc against your Fly Redis (e.g. for debugging):

```bash
fly ssh console -a tcdd-telegram -C "python scripts/check_alarms.py"
```

## Access control

By default the bot is **open** — anyone who finds it can use it. To restrict it
to specific people, set `ALLOWED_CHAT_IDS` to a comma-separated list of Telegram
chat IDs:

```bash
flyctl secrets set ALLOWED_CHAT_IDS=12345,67890
```

- Empty / unset ⇒ open to everyone.
- When set, only those chat IDs (plus `ADMIN_CHAT_ID`, always allowed) may use
  the bot. Everyone else gets a "not authorized" reply that includes their own
  chat ID, and the attempt is logged.
- **Finding a chat ID**: have the person message the bot once and read the
  `blocked unauthorized chat_id=…` line in `fly logs`, ask them for the ID the
  bot replied with, or use `@userinfobot` on Telegram. Append it to the list to
  add them.

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
  checker.py           periodic alarm checker (runs in-process via JobQueue)
scripts/
  check_alarms.py      ad-hoc one-shot checker invocation
Dockerfile             Fly.io image
fly.toml               Fly.io app config
```
