"""OrderWorkflow: оркестрация одного заказа. Никакого ввода-вывода — только activities."""
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from activities import (charge_payment, create_shipment, get_shipment_status,
                            refund_payment, release_inventory, reserve_inventory,
                            send_notification)

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
    cause = getattr(err, "cause", None)
    t = getattr(cause, "type", None) or ""
    return t if t.startswith("Business") else None


@workflow.defn
class OrderWorkflow:
    @workflow.run
    async def run(self, order: dict) -> str:
        oid = order["order_id"]

        try:
            await workflow.execute_activity(
                reserve_inventory, {"order_id": oid, "items": order["items"]}, **OPTS)
        except ActivityError as e:
            if business_type(e) != "Business409":
                raise
            await workflow.execute_activity(
                send_notification,
                {"order_id": oid, "status": "cancelled", "reason": "out_of_stock"}, **OPTS)
            return "cancelled"

        key = f"pay-{oid}-{workflow.uuid4().hex[:8]}"
        try:
            await workflow.execute_activity(
                charge_payment,
                {"order_id": oid, "amount": order["amount"], "idempotency_key": key}, **OPTS)
        except ActivityError as e:
            if business_type(e) != "Business402":
                raise
            await workflow.execute_activity(release_inventory, oid, **OPTS)
            await workflow.execute_activity(
                send_notification,
                {"order_id": oid, "status": "cancelled", "reason": "payment_declined"}, **OPTS)
            return "cancelled"

        sh = await workflow.execute_activity(create_shipment, oid, **OPTS)
        sid = sh["shipment_id"]
        status = "preparing"
        while status not in ("delivered", "failed"):
            await workflow.sleep(1)
            status = await workflow.execute_activity(get_shipment_status, sid, **OPTS)

        if status == "failed":
            await workflow.execute_activity(refund_payment, oid, **OPTS)
            await workflow.execute_activity(release_inventory, oid, **OPTS)
            await workflow.execute_activity(
                send_notification,
                {"order_id": oid, "status": "cancelled", "reason": "shipping_failed"}, **OPTS)
            return "cancelled"

        await workflow.execute_activity(
            send_notification, {"order_id": oid, "status": "completed", "reason": None}, **OPTS)
        return "completed"
