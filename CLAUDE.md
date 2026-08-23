# CLAUDE.md

Guidance for working in this repository.

<!-- caveman-mode: keep this block in sync across repos -->
## Caveman mode

Default to caveman. Short. No filler.

- No preamble, no recap, no "I'll now…". Lead with the answer.
- Status is fragments, not sentences: "Bug found." "Fix. Test." "Done."
- No closing summary unless asked. Never restate the diff in bullets.
- Tradeoffs, pushback, and explanations: plain English, still short. Not caveman —
  clarity wins there.
- Terse ≠ skipping work. Required gates (tests, lint, type-check, CI, deploy checks)
  still run and still get reported — just briefly.
- Code, comments, commit messages, and PR bodies are unaffected. Caveman is chat only.

## What this is

An interactive Telegram bot for **TCDD** (Turkish State Railways) train-ticket
search and seat-availability **alarms** (Python 3.12, asyncio,
`python-telegram-bot`). Users run `/search` to list trains with free seats or
`/alarm` to be notified when seats open up. Runs as a long-polling bot in a
Docker container on a VPS **in Turkey** (TCDD 403s foreign IPs — see
Conventions); state lives in a Redis container next to it.

> Personal/educational project — **not affiliated with TCDD**. It reads TCDD's
> public web API. See the README disclaimer.

## Architecture

- **Bot** — [src/tcdd_bot/main.py](src/tcdd_bot/main.py): `python-telegram-bot`
  long-polling, graceful shutdown (closes the TCDD session and Redis).
- **Checker** — [src/tcdd_bot/checker.py](src/tcdd_bot/checker.py): runs
  **in-process** via PTB's `JobQueue` every `CHECK_INTERVAL_MIN` minutes (with a
  random startup jitter). The same logic is callable one-off via
  [scripts/check_alarms.py](scripts/check_alarms.py).
- **State** — Redis ([store.py](src/tcdd_bot/store.py)): users, alarms, and
  per-user rate limits. There is **no database and no unbounded in-memory
  state** — everything durable is in Redis, and the process is otherwise stateless.
- **TCDD client** — [src/tcdd_bot/tcdd.py](src/tcdd_bot/tcdd.py). Two backends
  behind one `Protocol`: `LiveBackend` (real API via `curl_cffi` with Chrome ja3
  impersonation — TCDD's edge 403s non-browser TLS) and `StubBackend`
  (deterministic fakes for local dev; `TCDD_MODE=stub`).

## Layout

```
src/tcdd_bot/
  main.py        entrypoint, long-polling, JobQueue wiring, shutdown
  config.py      env loading (all tunables)
  tcdd.py        TCDD search client (Stub/Live backends, baked bearer token)
  stations.py    station catalog (CDN) + rapidfuzz match
  store.py       Redis store (users / alarms / rate limits)
  format.py      Telegram message rendering
  checker.py     periodic alarm checker (in-process via JobQueue)
  handlers/      start, search, alarm, ops, common
scripts/check_alarms.py   ad-hoc one-shot checker
Dockerfile                bot image
docker-compose.yml        bot + redis stack; .prod / .test overlays
deploy/redis.conf         Redis persistence settings
Makefile                  deploy / logs / backup / test-stack helpers
tests/                    pytest (fakeredis + StubBackend; no network/Redis)
```

## Config / secrets

All config is environment variables, loaded in
[config.py](src/tcdd_bot/config.py). Secrets live in `.env` on the server
(chmod 600, gitignored, never committed); [.env.example](.env.example) is the
template. `.env.test` holds the separate test bot's token.

| Var                  | Purpose                                                  |
| -------------------- | -------------------------------------------------------- |
| `BOT_TOKEN`          | Telegram bot token (secret)                              |
| `REDIS_URL`          | Redis connection URL (`redis://redis:6379/0` in compose) |
| `ALLOWED_CHAT_IDS`   | Comma-separated allow-list; **empty ⇒ open to everyone** |
| `ADMIN_CHAT_ID`      | Always allowed; receives failure warnings                |
| `TCDD_MODE`          | `live` (default) or `stub`                               |
| `CHECK_INTERVAL_MIN` | Checker cadence (default 10)                             |

## Conventions

- **No Claude attribution.** Do not add `Co-Authored-By: Claude` trailers to
  commits or "Generated with Claude Code" to PR bodies.
- Land changes via a branch → PR → squash-merge, not direct commits to `main`.
- `src/` layout; install editable (`pip install -e '.[dev]'`). Async throughout.
- **Config is centralized** in `config.py` — add a setting there, don't scatter
  `os.getenv`.
- **Access control fails open by default** (empty `ALLOWED_CHAT_IDS` ⇒ everyone);
  set the allow-list to restrict. `ADMIN_CHAT_ID` is always allowed.
- **Baked TCDD token** (tcdd.py): the hardcoded JWT is the _same public token
  TCDD's own frontend ships_; its `exp` is in 2024 but the gateway doesn't
  validate it. It is **not a personal credential** — leave it in code; if TCDD
  rotates it, re-extract from the production JS (see the in-file comment).
- **HTTP hygiene**: TCDD calls use `curl_cffi` `AsyncSession` (closed on
  shutdown); everything else uses `async with httpx.AsyncClient(...)`. Close what
  you open — this is a long-lived process.
- **The host must be in Turkey.** TCDD's API returns a bare nginx `403` to every
  foreign IP, regardless of TLS fingerprint, headers, or token — ja3 impersonation
  is a separate problem and does not help here. Before deploying anywhere new,
  run `curl -so /dev/null -w '%{http_code}' https://web-api-prod-ytp.tcddtasimacilik.gov.tr/`
  on that host; `403` means stop. `ebilet.…` answers 200 everywhere, so don't
  test with it. See the README's Hosting requirement table.
- **Publish no ports.** The bot is outbound-only and Redis is reachable only on
  the compose network, so the stack can sit next to other services without
  touching host firewall or sysctl state. Keep it that way — a `ports:` mapping
  would punch straight through the host firewall, since Docker bypasses it.
- **Two stacks, one repo.** `make up` runs prod (`.env`, live TCDD);
  `make test-up` runs the stub backend under a separate bot token with its own
  network and Redis volume. Run one at a time — the box has 1 GB and no swap.

## Quality gates (mirror CI in [.github/workflows/test.yml](.github/workflows/test.yml))

```bash
pip install -e '.[dev]'
ruff check .                              # lint
bandit -q -r src scripts -ll             # security lint (medium+)
pytest -q --cov=src --cov-report=term-missing   # tests + coverage (fail_under = 60)
pip-audit -r requirements.lock --strict  # dependency CVE audit
```

CI also runs a full-history **gitleaks** secret scan. Dependabot keeps pip,
GitHub Actions, and Docker deps current (weekly). The `fail_under = 60` coverage
floor is a regression ratchet — the live TCDD/network paths can't be unit-tested
without the real service.
