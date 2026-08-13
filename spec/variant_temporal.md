# Вариант реализации: Temporal (Python SDK)

## Стек

- Temporal-сервер УЖЕ запущен: gRPC `localhost:7233` (есть в `TEMPORAL_ADDRESS`),
  namespace `default`, Web-UI: http://localhost:8233.
- Python из `./.venv` (стоят `temporalio==1.31.0` и `httpx==0.28.1`).
- Архитектура обязательна: каждый заказ — отдельный workflow; все HTTP-вызовы —
  в activities; ретраи — через RetryPolicy Temporal (не пиши свои циклы ретраев).

## Рекомендуемая структура

```
solution/
├── run.sh          # точка входа (контракт в SPEC, раздел 9)
├── activities.py   # HTTP-вызовы моков (httpx) — весь ввод-вывод здесь
├── workflows.py    # OrderWorkflow — чистая оркестрация, без httpx
├── worker.py       # регистрирует workflow + activities, слушает task queue
└── starter.py      # получает заказы, запускает workflow'ы, ждёт результаты
```

`run.sh` для этого варианта (worker в фоне, starter в форграунде):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
../.venv/bin/python worker.py &
WORKER_PID=$!
trap 'kill $WORKER_PID 2>/dev/null || true; wait $WORKER_PID 2>/dev/null || true' EXIT
sleep 2   # дать worker'у подключиться
../.venv/bin/python starter.py
```

## Шпаргалка по стеку

`activities.py` — все вызовы HTTP; бизнес-ошибки помечай non_retryable:

```python
import os, httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

BASE = os.environ.get("MOCKS_URL", "http://localhost:8100")

async def _call(method, path, json=None, headers=None):
    # trust_env=False обязательно: иначе системный прокси macOS перехватит
    # запросы к localhost и они не дойдут до моков
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        r = await client.request(method, BASE + path, json=json, headers=headers)
    if r.status_code < 400:
        return r.json()
    if r.status_code in (400, 402, 404, 409, 422):
        # бизнес-ошибка: Temporal НЕ будет её ретраить (non_retryable),
        # workflow поймает её и уйдёт в ветку компенсации
        raise ApplicationError(f"HTTP {r.status_code}: {r.text}",
                               type=f"Business{r.status_code}", non_retryable=True)
    raise ApplicationError(f"HTTP {r.status_code}", type="ServerError")  # 5xx — ретраится политикой

@activity.defn
async def reserve_inventory(inp: dict) -> dict:
    return await _call("POST", "/api/inventory/reserve", json=inp)
# ... остальные activities аналогично, по одной на вызов API
```

`workflows.py` — оркестрация; httpx сюда импортировать НЕЛЬЗЯ (песочница
workflow запрещает недетерминированные импорты — заворачивай их так):

```python
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from activities import reserve_inventory, charge_payment  # и остальные

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=0.5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=4),
    maximum_attempts=5,
    non_retryable_error_types=["Business400", "Business402", "Business404",
                               "Business409", "Business422"],
)
OPTS = {"start_to_close_timeout": timedelta(seconds=10), "retry_policy": RETRY}

def business_type(err: ActivityError):
    """Тип бизнес-ошибки из упавшей activity ('Business402'...) или None."""
    cause = getattr(err, "cause", None)
    t = getattr(cause, "type", None) or ""
    return t if t.startswith("Business") else None

@workflow.defn
class OrderWorkflow:
    @workflow.run
    async def run(self, order: dict) -> str:
        oid = order["order_id"]
        try:
            await workflow.execute_activity(reserve_inventory,
                {"order_id": oid, "items": order["items"]}, **OPTS)
        except ActivityError as e:
            if business_type(e) != "Business409":
                raise
            # ... ветка отмены: notify cancelled, return
        key = f"pay-{oid}-{workflow.uuid4().hex[:8]}"   # детерминированный uuid — только так
        # ... charge с этим ключом; Business402 → release + notify cancelled
        # ... create_shipment; затем опрос:
        #     status = "preparing"
        #     while status not in ("delivered", "failed"):
        #         await workflow.sleep(1)               # durable-таймер вместо time.sleep
        #         status = await workflow.execute_activity(get_shipment_status, sid, **OPTS)
        # ... failed → refund + release + notify cancelled; delivered → notify completed
```

Важно: внутри workflow запрещены `time.sleep`, `uuid.uuid4()`, `random`,
`datetime.now()` — вместо них `workflow.sleep()`, `workflow.uuid4()`,
`workflow.now()`. В activities ограничений нет.

`worker.py`:

```python
import asyncio, os
from temporalio.client import Client
from temporalio.worker import Worker
import activities
from workflows import OrderWorkflow

async def main():
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"))
    worker = Worker(client, task_queue="orders",
                    workflows=[OrderWorkflow],
                    activities=[activities.reserve_inventory, ...])  # перечисли все
    print("worker запущен")
    await worker.run()

asyncio.run(main())
```

`starter.py` — конкурентный запуск всех заказов:

```python
import asyncio, os, sys, time, httpx
from temporalio.client import Client
from workflows import OrderWorkflow

async def main():
    base = os.environ.get("MOCKS_URL", "http://localhost:8100")
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        orders = (await c.get(base + "/api/orders")).json()
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"))
    stamp = int(time.time())
    async def one(o):
        return await client.execute_workflow(
            OrderWorkflow.run, o,
            id=f"order-{o['order_id']}-{stamp}",   # уникальный id на прогон
            task_queue="orders")
    results = await asyncio.gather(*(one(o) for o in orders), return_exceptions=True)
    failed = [r for r in results if isinstance(r, BaseException)]
    for r in results: print(r)
    return 1 if failed else 0

sys.exit(asyncio.run(main()))
```

## Отладка

- Web-UI http://localhost:8233 — история каждого workflow: какая activity
  упала, с каким сообщением, сколько было ретраев.
- `make state` — что моки думают о каждом заказе; `make ledger ORDER=ORD-1001` —
  журнал вызовов конкретного заказа.
- Отчёт чекера в конце `make verify` называет заказ и конкретную претензию.
- Изменил код — просто перезапусти `make verify` (worker стартует заново из run.sh).

## Чеклист перед verify

- [ ] Каждый заказ — отдельный workflow; запускаются конкурентно (gather в starter).
- [ ] Все HTTP-вызовы в activities; в workflows.py нет httpx/uuid/time.
- [ ] Ретраи — RetryPolicy; Business402/409 в non_retryable_error_types.
- [ ] Idempotency-Key создаётся в workflow один раз (workflow.uuid4()).
- [ ] Обе компенсации при провале доставки: refund И release.
- [ ] Уведомление — последним шагом при любом исходе.
- [ ] run.sh убивает worker по завершении и выходит с кодом 0.
