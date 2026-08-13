"""Starter фазы 2: детерминированные workflow id (RUN_ID) + подцепление к существующим.

Ручка для негативной проверки чекера — файл REF_BUG рядом (или env REF_BUG):
  stamped_ids — id меняется при каждом старте процесса → после рестарта
                заводятся НОВЫЕ workflow'ы и уже оплаченные заказы
                списываются повторно (двойное списание)
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from workflows import OrderWorkflow


def _bug():
    p = Path(__file__).with_name("REF_BUG")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("REF_BUG", "")


async def main():
    base = os.environ.get("MOCKS_URL", "http://localhost:8100")
    run_id = os.environ.get("RUN_ID", "dev")
    if _bug() == "stamped_ids":
        # намеренная ошибка: «прогон» перестаёт переживать рестарт процесса
        run_id = f"{run_id}-{time.time_ns()}"
        print("⚠ REF_BUG активен: stamped_ids", file=sys.stderr)

    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        orders = (await c.get(base + "/api/orders")).json()

    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"))

    async def one(order):
        wf_id = "order-{}-{}".format(order["order_id"], run_id)
        try:
            handle = await client.start_workflow(
                OrderWorkflow.run, order, id=wf_id, task_queue="orders",
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(wf_id)
        return order["order_id"], await handle.result()

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
