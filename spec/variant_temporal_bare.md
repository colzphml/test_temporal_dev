# Вариант реализации: Temporal (Python SDK)

- Temporal-сервер УЖЕ запущен: gRPC `localhost:7233` (есть в `TEMPORAL_ADDRESS`),
  namespace `default`; Web-UI: http://localhost:8233.
- Python из `./.venv` (установлены `temporalio==1.31.0` и `httpx==0.28.1`).
- Архитектурные требования: каждый заказ — отдельный workflow; весь HTTP-ввод-
  вывод — в activities; ретраи — политиками Temporal, а не собственными циклами.
- Решение — в `solution/`; точка входа `solution/run.sh` (контракт в SPEC,
  раздел 9). run.sh сам поднимает всё, что нужно решению (worker и т.п.),
  и убивает свои фоновые процессы при выходе.
- Отладка: Web-UI показывает историю каждого workflow (упавшие activity,
  ретраи, сообщения ошибок); `make state`, `make ledger ORDER=ORD-1001`.
- Замечание об окружении: системный HTTP-прокси macOS может перехватывать
  запросы python-клиентов к localhost. `make verify` выставляет NO_PROXY для
  твоего процесса сам; при ручном запуске своих скриптов учитывай это
  самостоятельно (NO_PROXY / настройки http-клиента).
