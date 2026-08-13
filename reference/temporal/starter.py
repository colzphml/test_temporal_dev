"""Starter: берёт заказы из моков, конкурентно запускает workflow'ы, ждёт результаты."""
import asyncio
import os
import sys
import time

import httpx
from temporalio.client import Client

from workflows import OrderWorkflow


async def main():
    base = os.environ.get("MOCKS_URL", "http://localhost:8100")
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        orders = (await c.get(base + "/api/orders")).json()

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"))

    stamp = int(time.time())

    async def one(order):
        result = await client.execute_workflow(
            OrderWorkflow.run, order,
            id="order-{}-{}".format(order["order_id"], stamp),
            task_queue="orders")
        return order["order_id"], result

    results = await asyncio.gather(*(one(o) for o in orders), return_exceptions=True)
    failed = 0
    for r in results:
        if isinstance(r, BaseException):
            print(f"ОШИБКА: {r}", file=sys.stderr)
            failed += 1
        else:
            print(f"{r[0]}: {r[1]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
