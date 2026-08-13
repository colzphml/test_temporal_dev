.DEFAULT_GOAL := help
SHELL := /bin/bash

# ── Стенд ─────────────────────────────────────────────────────────────
up: ## поднять стенд (temporal + моки) и дождаться готовности
	@python3 scripts/preflight.py
	docker compose up -d --build
	@python3 scripts/wait_ready.py
	@$(MAKE) --no-print-directory urls

down: ## остановить стенд
	docker compose down

clean: ## остановить стенд и удалить данные (volumes)
	docker compose down -v

ps: ## статус контейнеров
	docker compose ps

logs: ## логи стенда (Ctrl+C для выхода)
	docker compose logs --tail 100 -f

reset: ## сбросить состояние моков вручную (verify делает это сам)
	@curl -s -X POST http://localhost:8100/admin/reset > /dev/null && echo "моки сброшены"

urls: ## адреса сервисов
	@echo "Моки бизнес-сервисов:  http://localhost:8100  (отладка: /admin/state, /admin/ledger)"
	@echo "Temporal gRPC:         localhost:7233"
	@echo "Temporal UI:           http://localhost:8233"

# ── Эксперимент (протокол: EXPERIMENT.md) ─────────────────────────────
workspace: _need_variant ## создать воркспейс: make workspace VARIANT=python|temporal [PHASE=2|3] [FORCE=1]
	@python3 scripts/workspace.py --variant $(VARIANT) $(if $(PHASE),--phase $(PHASE),) $(if $(FORCE),--force,)

begin: _need_variant ## зафиксировать старт прогона: make begin VARIANT=... [MODEL="..."]
	@python3 scripts/timer.py begin --variant $(VARIANT) $(if $(MODEL),--model "$(MODEL)",)

verify: _need_variant ## проверить решение: make verify VARIANT=...
	@python3 scripts/verify.py --variant $(VARIANT)

report: ## таблица результатов всех прогонов
	@python3 scripts/timer.py report

archive: _need_variant ## сохранить решение прогона в results/archive/
	@ts=$$(date +%Y%m%d-%H%M%S); dest="results/archive/$(VARIANT)-$$ts"; \
	if [ -d "runs/$(VARIANT)/solution" ] && [ -n "$$(ls -A runs/$(VARIANT)/solution 2>/dev/null)" ]; then \
		mkdir -p "$$dest"; cp -R runs/$(VARIANT)/solution "$$dest/"; \
		if [ -f "runs/$(VARIANT)/TASK.md" ]; then cp "runs/$(VARIANT)/TASK.md" "$$dest/"; fi; \
		echo "решение сохранено в $$dest"; \
	else echo "в runs/$(VARIANT)/solution пусто — нечего сохранять"; fi

selftest: ## самопроверка стенда эталонными решениями (перед серией прогонов)
	@python3 scripts/selftest.py

_need_variant:
	@test -n "$(VARIANT)" || { echo "укажи VARIANT=python или VARIANT=temporal"; exit 1; }

help: ## эта справка
	@grep -E '^[a-z][a-zA-Z_-]*:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

.PHONY: up down clean ps logs reset urls workspace begin verify report archive selftest _need_variant help
