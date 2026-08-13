"""Worker: слушает task queue 'orders', исполняет workflow и activities."""
import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from activities import ALL_ACTIVITIES
from workflows import OrderWorkflow


async def main():
    client = await Client.connect(
        os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"))
    worker = Worker(client, task_queue="orders",
                    workflows=[OrderWorkflow], activities=ALL_ACTIVITIES)
    print("worker запущен, очередь 'orders'")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
