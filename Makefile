# Deploy helpers for the DigitalOcean droplet. Run from the repo root.
#
#   make deploy      pull + rebuild + restart production
#   make logs        follow production logs
#   make test-up     start the isolated test stack (stub backend, test bot)
#   make test-down   stop it and delete its Redis volume

COMPOSE      := docker compose -f docker-compose.yml -f docker-compose.prod.yml
TEST_COMPOSE := COMPOSE_PROJECT_NAME=tcdd-test docker compose -p tcdd-test \
                -f docker-compose.yml -f docker-compose.test.yml
BACKUP_DIR   := backups

.PHONY: up down restart deploy logs ps build backup test-up test-down test-logs

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart bot

build:
	$(COMPOSE) build

deploy:
	git pull --ff-only
	$(COMPOSE) up -d --build
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

# Snapshot Redis to dump.rdb and copy it out. SAVE (not BGSAVE) so the file is
# guaranteed complete before the copy; the dataset is a handful of keys, so the
# blocking write is instant. Cron this daily.
backup:
	@mkdir -p $(BACKUP_DIR)
	$(COMPOSE) exec -T redis redis-cli SAVE
	$(COMPOSE) cp redis:/data/dump.rdb $(BACKUP_DIR)/dump-$$(date +%F).rdb
	@ls -t $(BACKUP_DIR)/dump-*.rdb | tail -n +8 | xargs -r rm --
	@echo "backup -> $(BACKUP_DIR)/dump-$$(date +%F).rdb"

# --- test stack (own network + own Redis volume) ---

test-up:
	$(TEST_COMPOSE) up -d --build
	$(TEST_COMPOSE) ps

test-logs:
	$(TEST_COMPOSE) logs -f --tail=100

test-down:
	$(TEST_COMPOSE) down -v
