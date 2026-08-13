"""Активности: весь HTTP-ввод-вывод. Бизнес-ошибки — non_retryable ApplicationError."""
import os

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

BASE = os.environ.get("MOCKS_URL", "http://localhost:8100")


async def _call(method, path, json=None, headers=None):
    # trust_env=False: системный прокси не должен перехватывать запросы к localhost
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        r = await client.request(method, BASE + path, json=json, headers=headers)
    if r.status_code < 400:
        return r.json()
    if r.status_code in (400, 402, 404, 409, 422):
        raise ApplicationError(f"HTTP {r.status_code}: {r.text}",
                               type=f"Business{r.status_code}", non_retryable=True)
    raise ApplicationError(f"HTTP {r.status_code}: {r.text}", type="ServerError")


@activity.defn
async def reserve_inventory(inp: dict) -> dict:
    return await _call("POST", "/api/inventory/reserve", json=inp)


@activity.defn
async def release_inventory(order_id: str) -> dict:
    return await _call("POST", "/api/inventory/release", json={"order_id": order_id})


@activity.defn
async def charge_payment(inp: dict) -> dict:
    return await _call("POST", "/api/payments/charge",
                       json={"order_id": inp["order_id"], "amount": inp["amount"]},
                       headers={"Idempotency-Key": inp["idempotency_key"]})


@activity.defn
async def refund_payment(order_id: str) -> dict:
    return await _call("POST", "/api/payments/refund", json={"order_id": order_id})


@activity.defn
async def create_shipment(order_id: str) -> dict:
    return await _call("POST", "/api/shipping/shipments", json={"order_id": order_id})


@activity.defn
async def get_shipment_status(shipment_id: str) -> str:
    res = await _call("GET", f"/api/shipping/shipments/{shipment_id}")
    return res["status"]


@activity.defn
async def send_notification(inp: dict) -> dict:
    return await _call("POST", "/api/notifications/notify", json=inp)


ALL_ACTIVITIES = [reserve_inventory, release_inventory, charge_payment,
                  refund_payment, create_shipment, get_shipment_status,
                  send_notification]
