#!/usr/bin/env python3
"""Эталонное решение (чистый Python, asyncio). Используется селфтестом харнесса.

Ручка для негативных проверок чекера — файл REF_BUG рядом (или env REF_BUG):
  no_refund            — «забыть» refund при провале доставки
  new_key_per_attempt  — новый Idempotency-Key на каждую попытку charge
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx

BASE = os.environ.get("MOCKS_URL", "http://localhost:8100")
MAX_ATTEMPTS = 5


def _bug():
    p = Path(__file__).with_name("REF_BUG")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("REF_BUG", "")


BUG = _bug()


class BusinessError(Exception):
    def __init__(self, status_code, payload):
        super().__init__(f"HTTP {status_code}: {payload}")
        self.status_code = status_code
        self.payload = payload


async def call(client, method, path, json=None, headers=None):
    delay = 0.5
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            r = await client.request(method, BASE + path, json=json, headers=headers)
            if r.status_code < 400:
                return r.json()
            if r.status_code in (400, 402, 404, 409, 422):
                raise BusinessError(r.status_code, r.json())
        except httpx.TransportError:
            pass
        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(f"{method} {path}: попытки исчерпаны")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 4)


async def notify(client, oid, status, reason=None):
    await call(client, "POST", "/api/notifications/notify",
               json={"order_id": oid, "status": status, "reason": reason})


async def charge(client, order):
    oid = order["order_id"]
    if BUG == "new_key_per_attempt":
        # намеренная ошибка: свой ключ на каждую попытку → двойное списание
        delay = 0.5
        for attempt in range(1, MAX_ATTEMPTS + 1):
            key = f"pay-{oid}-{uuid.uuid4().hex[:8]}"
            r = await client.post(BASE + "/api/payments/charge",
                                  json={"order_id": oid, "amount": order["amount"]},
                                  headers={"Idempotency-Key": key})
            if r.status_code < 400:
                return r.json()
            if r.status_code in (400, 402, 404, 409, 422):
                raise BusinessError(r.status_code, r.json())
            await asyncio.sleep(delay)
            delay = min(delay * 2, 4)
        raise RuntimeError("charge: попытки исчерпаны")
    key = f"pay-{oid}-{uuid.uuid4().hex[:8]}"
    return await call(client, "POST", "/api/payments/charge",
                      json={"order_id": oid, "amount": order["amount"]},
                      headers={"Idempotency-Key": key})


async def process_order(client, order):
    oid = order["order_id"]
    try:
        await call(client, "POST", "/api/inventory/reserve",
                   json={"order_id": oid, "items": order["items"]})
    except BusinessError:
        await notify(client, oid, "cancelled", "out_of_stock")
        return oid, "cancelled"
    try:
        await charge(client, order)
    except BusinessError:
        await call(client, "POST", "/api/inventory/release", json={"order_id": oid})
        await notify(client, oid, "cancelled", "payment_declined")
        return oid, "cancelled"
    sh = await call(client, "POST", "/api/shipping/shipments", json={"order_id": oid})
    sid = sh["shipment_id"]
    while True:
        st = (await call(client, "GET", f"/api/shipping/shipments/{sid}"))["status"]
        if st in ("delivered", "failed"):
            break
        await asyncio.sleep(1)
    if st == "failed":
        if BUG != "no_refund":
            await call(client, "POST", "/api/payments/refund", json={"order_id": oid})
        await call(client, "POST", "/api/inventory/release", json={"order_id": oid})
        await notify(client, oid, "cancelled", "shipping_failed")
        return oid, "cancelled"
    await notify(client, oid, "completed")
    return oid, "completed"


async def process_safe(client, order):
    oid = order["order_id"]
    try:
        return await process_order(client, order)
    except Exception as e:  # noqa: BLE001 — не даём одному заказу уронить батч
        print(f"[{oid}] ОШИБКА: {e}", file=sys.stderr)
        return oid, "error"


async def main():
    if BUG:
        print(f"⚠ REF_BUG активен: {BUG}", file=sys.stderr)
    # trust_env=False: системный прокси не должен перехватывать запросы к localhost
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        orders = await call(client, "GET", "/api/orders")
        results = await asyncio.gather(*(process_safe(client, o) for o in orders))
    for oid, st in results:
        print(f"{oid}: {st}")
    return 1 if any(st == "error" for _, st in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
