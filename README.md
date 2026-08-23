# tcdd-telegram

Interactive Telegram bot for TCDD train ticket search + alarms.

> **Disclaimer.** Personal, educational project — **not affiliated with, endorsed by, or
> connected to TCDD**. It reads TCDD's public web API to monitor seat availability for
> personal use; you are responsible for complying with TCDD's terms of service and
> applicable law. The bundled API token is the same public token TCDD's own website ships
> in its frontend and may stop working at any time. Provided "as is", without warranty —
> use at your own risk. Licensed under the [MIT License](LICENSE).

## What it does

- `/search` — Nereden / Nereye / Tarih(ler) / Yolcu sayısı seçtir, boş koltuklu trenleri listele, TCDD'ye deeplink. Birden fazla gün seçilebilir.
- `/alarm` — aynı parametreleri al, alarm kur. Birden fazla gün seçilebilir; yalnızca seçilen günler için kontrol edilir. İstersen belirli tren(ler) seçip sadece onları izleyebilirsin (seçilen tren no'ları tüm seçili günlerde geçerli). Boş yer çıkınca uyarır.
- `/alarms`, `/clear`, `/pause`, `/resume` — alarm yönetimi.
- Tekerlekli sandalye koltukları sayımdan çıkarılır.
- Kullanıcı başına en fazla 5 aktif alarm, saatte 10 arama.

## Architecture

- **Bot** (`src/tcdd_bot/main.py`) — `python-telegram-bot` long-polling, running as a
  Docker container on a VPS **in Turkey** (see [Hosting requirement](#hosting-requirement)).
- **Checker** (`src/tcdd_bot/checker.py`) — runs inside the bot process via PTB's
  `JobQueue`, every `CHECK_INTERVAL_MIN` minutes (default 10) with a random
  initial jitter. Same code is also callable as a one-off via
  `scripts/check_alarms.py`.
- **State** — Redis container alongside the bot on the same host (AOF-persisted,
  reachable only on the compose-private network).
- **TCDD client** — `src/tcdd_bot/tcdd.py`. Two backends:
  - `LiveBackend` (default): real TCDD JSON API at `web-api-prod-ytp.tcddtasimacilik.gov.tr/tms`. Uses `curl_cffi` with Chrome ja3 impersonation because TCDD's edge ja3-fingerprints non-browser clients.
  - `StubBackend`: deterministic fake trains for local development. Set `TCDD_MODE=stub` to use.

## Hosting requirement

**The server must be in Turkey.** TCDD's API host answers every request from a
foreign IP with a bare nginx `403 Forbidden` — no matter the TLS fingerprint,
headers, or token. Verified with plain `curl` from several origins:

| Origin | `GET web-api-prod-ytp…/` |
| --- | --- |
| Home connection in Turkey | `200` |
| Turkish datacenter (check-host.net `tr1`/`tr2` nodes) | `200` |
| DigitalOcean, Frankfurt | `403` |
| Railway, USA | `403` |
| German datacenter (check-host.net `de1`) | `403` |

The filter is geographic, not residential-vs-datacenter: a Turkish VPS works
fine. Note that `ebilet.tcddtasimacilik.gov.tr` answers `200` from anywhere —
only the API host is filtered, so being able to load the website proves nothing.

Before deploying to any new host, check it there first:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://web-api-prod-ytp.tcddtasimacilik.gov.tr/
```

`403` means stop — no configuration or code change fixes it. Symptoms if you
deploy anyway: `/search` replies "arama sırasında bir sorun oluştu" and the log
shows `RuntimeError: TCDD HTTP 403`.

## Setup

### 1. Telegram bot

Create one via @BotFather, copy the token.

### 2. Redis

Redis runs as a container in the same compose stack (see
[docker-compose.yml](docker-compose.yml)). Nothing to provision: `REDIS_URL` is
just `redis://redis:6379/0`. It persists to an AOF file on a named volume, so
alarms survive restarts and reboots.

### 3. Local dev

```bash
cd ~/Projects/tcdd-telegram
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill in BOT_TOKEN, ADMIN_CHAT_ID; point REDIS_URL at a local Redis
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

### 4. Deploy to a server

Any Linux host with Docker + the compose plugin, **located in Turkey** (see
[Hosting requirement](#hosting-requirement)). The stack publishes **no ports** —
the bot is outbound-only (Telegram long-polling + TCDD) and Redis is reachable
only on the stack's private network — so it drops in next to existing services
without touching the host firewall.

```bash
git clone https://github.com/mkakpinar/tcdd-telegram.git /opt/tcdd
cd /opt/tcdd
cp .env.example .env && chmod 600 .env
# fill in BOT_TOKEN, ADMIN_CHAT_ID, ALLOWED_CHAT_IDS
make up
make logs
```

Redeploy after a change: `make deploy` (git pull + rebuild + restart).

Back up Redis daily with `make backup` (keeps the last 7 dumps):

```
0 4 * * * cd /opt/tcdd && make backup >> /var/log/tcdd-backup.log 2>&1
```

### 5. Test stack

A second stack runs the stub backend against a **separate bot token**, with its
own network and its own Redis volume, so it can never touch production state:

```bash
cp .env.example .env.test && chmod 600 .env.test
# fill in the TEST bot's BOT_TOKEN
make test-up && make test-logs
make test-down     # stops it and deletes its Redis volume
```

On a 1 GB box, run one stack at a time.

### 6. Periodic checker

The checker runs automatically inside the bot process. No extra setup — look for
"checker scheduled" / "checker: N active alarms" in `make logs`.

To run it ad-hoc:

```bash
docker compose exec bot python scripts/check_alarms.py
```

## Access control

By default the bot is **open** — anyone who finds it can use it. To restrict it
to specific people, set `ALLOWED_CHAT_IDS` to a comma-separated list of Telegram
chat IDs:

```bash
# in .env, then: make restart
ALLOWED_CHAT_IDS=12345,67890
```

- Empty / unset ⇒ open to everyone.
- When set, only those chat IDs (plus `ADMIN_CHAT_ID`, always allowed) may use
  the bot. Everyone else gets a "not authorized" reply that includes their own
  chat ID, and the attempt is logged.
- **Finding a chat ID**: have the person message the bot once and read the
  `blocked unauthorized chat_id=…` line in `make logs`, ask them for the ID the
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
  store.py             Redis-backed user/alarm/rate-limit store
  format.py            message rendering
  handlers/
    start.py           /start, /help
    search.py          /search conversation
    alarm.py           /alarm, /alarms, /clear, /pause, /resume
    common.py          shared inline keyboards
  checker.py           periodic alarm checker (runs in-process via JobQueue)
scripts/
  check_alarms.py      ad-hoc one-shot checker invocation
Dockerfile             bot image
docker-compose.yml     bot + redis stack (prod/test overlays alongside)
deploy/redis.conf      Redis persistence settings
Makefile               deploy / logs / backup / test-stack helpers
```
