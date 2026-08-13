# Вариант реализации: чистый Python (asyncio)

## Стек

- Python из `./.venv` (стоит `httpx==0.28.1`; стандартная библиотека — без ограничений).
- НИКАКИХ workflow-движков и оркестраторов (Temporal, Airflow, Celery и т.п.) —
  только собственный код на asyncio.
- Temporal-переменные окружения в этом варианте игнорируй.

## Рекомендуемая структура

```
solution/
├── run.sh      # точка входа (контракт в SPEC, раздел 9)
└── main.py     # вся логика (можно разбить на модули по вкусу)
```

`run.sh` для этого варианта:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec ../.venv/bin/python main.py
```

## Шпаргалка по стеку

Конкурентная обработка всех заказов:

```python
import asyncio, httpx

async def main():
    # trust_env=False обязательно: иначе системный прокси macOS перехватит
    # запросы к localhost и они не дойдут до моков
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        r = await client.get(f"{BASE}/api/orders")
        orders = r.json()
        results = await asyncio.gather(*(process_order(client, o) for o in orders))

asyncio.run(main())
```

Ретрай с бэкоффом и разделение «временная / бизнес-ошибка»:

```python
class BusinessError(Exception):
    def __init__(self, status_code, payload):
        self.status_code, self.payload = status_code, payload

async def call(client, method, path, json=None, headers=None):
    delay = 0.5
    for attempt in range(1, 6):                      # до 5 попыток
        try:
            r = await client.request(method, BASE + path, json=json, headers=headers)
            if r.status_code < 400:
                return r.json()
            if r.status_code in (400, 402, 404, 409, 422):
                raise BusinessError(r.status_code, r.json())   # не ретраим
        except httpx.TransportError:
            pass                                     # сетевые ошибки ретраим как 500
        if attempt == 5:
            raise RuntimeError(f"{method} {path}: попытки исчерпаны")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 4)
```

Идемпотентный платёж — ключ создаётся один раз на заказ, снаружи ретрая:

```python
import uuid
key = f"pay-{order['order_id']}-{uuid.uuid4().hex[:8]}"
await call(client, "POST", "/api/payments/charge",
           json={"order_id": oid, "amount": order["amount"]},
           headers={"Idempotency-Key": key})   # при ретраях внутри call() ключ один и тот же
```

Опрос доставки:

```python
while True:
    st = (await call(client, "GET", f"/api/shipping/shipments/{sid}"))["status"]
    if st in ("delivered", "failed"):
        break
    await asyncio.sleep(1)
```

## Отладка

- Печатай прогресс по заказам в stdout — увидишь его в выводе `make verify`.
- `make state` — что моки думают о каждом заказе; `make ledger ORDER=ORD-1001` —
  журнал вызовов конкретного заказа.
- Отчёт чекера в конце `make verify` называет заказ и конкретную претензию.

## Чеклист перед verify

- [ ] Заказы обрабатываются конкурентно (gather), не по одному.
- [ ] Ретраи на каждом шаге, включая notify; 402/409 не ретраятся.
- [ ] Idempotency-Key один на заказ, при ретраях не меняется.
- [ ] Обе компенсации при провале доставки: refund И release.
- [ ] Уведомление — последним шагом при любом исходе.
- [ ] run.sh завершается сам с кодом 0.
