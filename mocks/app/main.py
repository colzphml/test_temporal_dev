"""Моки бизнес-сервисов интернет-магазина: склад, платежи, доставка, уведомления.

Поведение детерминировано сценариями (scenarios.py) и счётчиками попыток,
поэтому прогоны воспроизводимы. Всё состояние в памяти, сброс — POST /admin/reset.
"""
import uuid
from typing import List, Literal, Optional

from fastapi import FastAPI, Header, Response
from pydantic import BaseModel

from .checker import run_checks
from .scenarios import ORDER_IDS, ORDERS, scenario, shipping_status
from .state import STATE

app = FastAPI(title="business-mocks", docs_url="/docs")


class Item(BaseModel):
    sku: str
    qty: int


class ReserveReq(BaseModel):
    order_id: str
    items: List[Item]


class ReleaseReq(BaseModel):
    order_id: str


class ChargeReq(BaseModel):
    order_id: str
    amount: float


class RefundReq(BaseModel):
    order_id: str


class ShipmentReq(BaseModel):
    order_id: str


class NotifyReq(BaseModel):
    order_id: str
    status: Literal["completed", "cancelled"]
    reason: Optional[str] = None


def _unknown_order(order_id: str, service: str, action: str, response: Response):
    STATE.log(service, action, order_id, 0, 404, False, error="unknown_order")
    response.status_code = 404
    return {"error": "unknown_order", "message": f"Заказ {order_id} не существует"}


# ---------------------------------------------------------------- orders

@app.get("/api/orders")
async def list_orders():
    return ORDERS


# ---------------------------------------------------------------- inventory

@app.post("/api/inventory/reserve")
async def reserve(req: ReserveReq, response: Response):
    if req.order_id not in ORDER_IDS:
        return _unknown_order(req.order_id, "inventory", "reserve", response)
    scen = scenario(req.order_id)
    n = STATE.next_attempt("inventory.reserve", req.order_id)

    if scen.get("stock", True) is False:
        STATE.log("inventory", "reserve", req.order_id, n, 409, False, error="out_of_stock")
        response.status_code = 409
        return {"error": "out_of_stock", "message": "Товара нет на складе, резерв невозможен"}

    if n <= scen.get("reserve_500_first", 0):
        STATE.log("inventory", "reserve", req.order_id, n, 500, False, error="internal_error")
        response.status_code = 500
        return {"error": "internal_error", "message": "Временный сбой сервиса склада, повторите запрос"}

    existing = STATE.reservations.get(req.order_id)
    if existing and not existing["released"]:
        STATE.log("inventory", "reserve", req.order_id, n, 200, True,
                  reservation_id=existing["reservation_id"], repeated=True)
        return {"reservation_id": existing["reservation_id"], "status": "reserved"}

    res = {
        "reservation_id": "res_" + uuid.uuid4().hex[:10],
        "items": [i.model_dump() for i in req.items],
        "released": False,
        "ts": STATE.now(),
        "release_ts": None,
    }
    STATE.reservations[req.order_id] = res
    STATE.log("inventory", "reserve", req.order_id, n, 200, True, reservation_id=res["reservation_id"])
    return {"reservation_id": res["reservation_id"], "status": "reserved"}


@app.post("/api/inventory/release")
async def release(req: ReleaseReq, response: Response):
    if req.order_id not in ORDER_IDS:
        return _unknown_order(req.order_id, "inventory", "release", response)
    n = STATE.next_attempt("inventory.release", req.order_id)
    res = STATE.reservations.get(req.order_id)

    if res is None:
        STATE.log("inventory", "release", req.order_id, n, 200, True, result="nothing_reserved")
        return {"status": "nothing_reserved"}
    if res["released"]:
        STATE.log("inventory", "release", req.order_id, n, 200, True, result="already_released")
        return {"status": "already_released"}

    res["released"] = True
    res["release_ts"] = STATE.now()
    STATE.log("inventory", "release", req.order_id, n, 200, True, result="released")
    return {"status": "released"}


# ---------------------------------------------------------------- payments

def _record_charge(order_id: str, amount: float, key: Optional[str]):
    ch = {
        "charge_id": "ch_" + uuid.uuid4().hex[:10],
        "amount": amount,
        "key": key,
        "ts": STATE.now(),
    }
    STATE.charges.setdefault(order_id, []).append(ch)
    if key:
        STATE.idempotency[key] = {"order_id": order_id, **ch}
    return ch


@app.post("/api/payments/charge")
async def charge(req: ChargeReq, response: Response,
                 idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    if req.order_id not in ORDER_IDS:
        return _unknown_order(req.order_id, "payments", "charge", response)
    scen = scenario(req.order_id)

    # Идемпотентный повтор: ключ уже видели — возвращаем записанное списание.
    if idempotency_key and idempotency_key in STATE.idempotency:
        known = STATE.idempotency[idempotency_key]
        n = STATE.next_attempt("payments.charge", req.order_id)
        if known["order_id"] != req.order_id:
            STATE.log("payments", "charge", req.order_id, n, 409, False,
                      error="idempotency_key_reused", key=idempotency_key)
            response.status_code = 409
            return {"error": "idempotency_key_reused",
                    "message": "Этот Idempotency-Key уже использован для другого заказа"}
        STATE.log("payments", "charge", req.order_id, n, 200, True,
                  charge_id=known["charge_id"], key=idempotency_key, idempotent_replay=True)
        return {"charge_id": known["charge_id"], "status": "succeeded", "idempotent_replay": True}

    n = STATE.next_attempt("payments.charge", req.order_id)

    if scen.get("charge") == "declined":
        STATE.log("payments", "charge", req.order_id, n, 402, False,
                  error="card_declined", key=idempotency_key)
        response.status_code = 402
        return {"error": "card_declined",
                "message": "Платёж отклонён банком. Повторные попытки бесполезны"}

    if n <= scen.get("charge_500_first", 0):
        if scen.get("charge_500_mode") == "recorded":
            # Списание записано, но ответ «потерян» — классическая ловушка идемпотентности.
            ch = _record_charge(req.order_id, req.amount, idempotency_key)
            STATE.log("payments", "charge", req.order_id, n, 500, False,
                      error="internal_error", recorded_anyway=True,
                      charge_id=ch["charge_id"], key=idempotency_key)
        else:
            STATE.log("payments", "charge", req.order_id, n, 500, False,
                      error="internal_error", key=idempotency_key)
        response.status_code = 500
        return {"error": "internal_error",
                "message": "Сбой платёжного шлюза. Повторите запрос с тем же Idempotency-Key"}

    ch = _record_charge(req.order_id, req.amount, idempotency_key)
    STATE.log("payments", "charge", req.order_id, n, 200, True,
              charge_id=ch["charge_id"], amount=req.amount, key=idempotency_key)
    return {"charge_id": ch["charge_id"], "status": "succeeded"}


@app.post("/api/payments/refund")
async def refund(req: RefundReq, response: Response):
    if req.order_id not in ORDER_IDS:
        return _unknown_order(req.order_id, "payments", "refund", response)
    n = STATE.next_attempt("payments.refund", req.order_id)
    charges = STATE.charges.get(req.order_id, [])

    if not charges:
        STATE.log("payments", "refund", req.order_id, n, 404, False, error="nothing_to_refund")
        response.status_code = 404
        return {"error": "nothing_to_refund", "message": "По заказу нет успешных списаний"}

    existing = STATE.refunds.get(req.order_id)
    if existing:
        STATE.log("payments", "refund", req.order_id, n, 200, True,
                  refund_id=existing[0]["refund_id"], repeated=True)
        return {"refund_id": existing[0]["refund_id"], "status": "refunded"}

    rf = {"refund_id": "rf_" + uuid.uuid4().hex[:10], "amount": charges[0]["amount"], "ts": STATE.now()}
    STATE.refunds[req.order_id] = [rf]
    STATE.log("payments", "refund", req.order_id, n, 200, True,
              refund_id=rf["refund_id"], amount=rf["amount"])
    return {"refund_id": rf["refund_id"], "status": "refunded"}


# ---------------------------------------------------------------- shipping

@app.post("/api/shipping/shipments", status_code=201)
async def create_shipment(req: ShipmentReq, response: Response):
    if req.order_id not in ORDER_IDS:
        return _unknown_order(req.order_id, "shipping", "create_shipment", response)
    scen = scenario(req.order_id)
    n = STATE.next_attempt("shipping.create", req.order_id)

    existing_id = STATE.shipment_by_order.get(req.order_id)
    if existing_id:
        STATE.log("shipping", "create_shipment", req.order_id, n, 200, True,
                  shipment_id=existing_id, repeated=True)
        response.status_code = 200
        return {"shipment_id": existing_id, "status": "accepted"}

    sid = "shp_" + uuid.uuid4().hex[:10]
    STATE.shipments[sid] = {
        "order_id": req.order_id,
        "profile": scen.get("shipping", "normal"),
        "created_ts": STATE.now(),
    }
    STATE.shipment_by_order[req.order_id] = sid
    STATE.log("shipping", "create_shipment", req.order_id, n, 201, True, shipment_id=sid)
    return {"shipment_id": sid, "status": "accepted"}


@app.get("/api/shipping/shipments/{shipment_id}")
async def get_shipment(shipment_id: str, response: Response):
    sh = STATE.shipments.get(shipment_id)
    if sh is None:
        STATE.log("shipping", "poll", None, 0, 404, False, error="unknown_shipment",
                  shipment_id=shipment_id)
        response.status_code = 404
        return {"error": "unknown_shipment", "message": f"Отправление {shipment_id} не существует"}
    status = shipping_status(sh["profile"], STATE.now() - sh["created_ts"])
    STATE.log("shipping", "poll", sh["order_id"], 0, 200, True,
              shipment_id=shipment_id, status=status)
    return {"shipment_id": shipment_id, "order_id": sh["order_id"], "status": status}


# ---------------------------------------------------------------- notifications

@app.post("/api/notifications/notify")
async def notify(req: NotifyReq, response: Response):
    if req.order_id not in ORDER_IDS:
        return _unknown_order(req.order_id, "notifications", "notify", response)
    scen = scenario(req.order_id)
    n = STATE.next_attempt("notifications.notify", req.order_id)

    if n <= scen.get("notify_500_first", 0):
        STATE.log("notifications", "notify", req.order_id, n, 500, False, error="internal_error")
        response.status_code = 500
        return {"error": "internal_error", "message": "Сбой сервиса уведомлений, повторите запрос"}

    note = {"status": req.status, "reason": req.reason, "ts": STATE.now()}
    STATE.notifications.setdefault(req.order_id, []).append(note)
    STATE.log("notifications", "notify", req.order_id, n, 200, True,
              status=req.status, reason=req.reason)
    return {"status": "accepted"}


# ---------------------------------------------------------------- admin

@app.post("/admin/reset")
async def admin_reset():
    STATE.reset()
    return {"status": "reset"}


@app.get("/admin/ledger")
async def admin_ledger(order_id: Optional[str] = None):
    if order_id:
        return [e for e in STATE.ledger if e["order_id"] == order_id]
    return STATE.ledger


@app.get("/admin/state")
async def admin_state():
    out = []
    for order in ORDERS:
        oid = order["order_id"]
        res = STATE.reservations.get(oid)
        sid = STATE.shipment_by_order.get(oid)
        shipment = None
        if sid:
            sh = STATE.shipments[sid]
            shipment = {
                "shipment_id": sid,
                "status_now": shipping_status(sh["profile"], STATE.now() - sh["created_ts"]),
            }
        out.append({
            "order_id": oid,
            "inventory": {
                "reserve_attempts": STATE.attempt_count("inventory.reserve", oid),
                "reserved": res is not None,
                "released": bool(res and res["released"]),
            },
            "payments": {
                "charge_attempts": STATE.attempt_count("payments.charge", oid),
                "successful_charges": [
                    {"charge_id": c["charge_id"], "amount": c["amount"]}
                    for c in STATE.charges.get(oid, [])
                ],
                "refunds": len(STATE.refunds.get(oid, [])),
            },
            "shipping": shipment,
            "notifications": STATE.notifications.get(oid, []),
        })
    return {"elapsed_since_reset": STATE.now(), "orders": out}


@app.get("/admin/check")
async def admin_check():
    ok, report = run_checks()
    return {"ok": ok, "report": report}
