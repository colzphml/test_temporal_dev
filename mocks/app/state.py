"""Состояние моков в памяти процесса. Всё обнуляется через POST /admin/reset."""
import time
from datetime import datetime, timezone


class State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.reset_mono = time.monotonic()
        self.ledger = []           # события всех вызовов API (журнал для чекера и отладки)
        self.attempts = {}         # (service.action, order_id) -> счётчик попыток
        self.reservations = {}     # order_id -> {reservation_id, items, released, ts, release_ts}
        self.charges = {}          # order_id -> [{charge_id, amount, key, ts}]
        self.idempotency = {}      # idempotency_key -> charge
        self.refunds = {}          # order_id -> [{refund_id, amount, ts}]
        self.shipments = {}        # shipment_id -> {order_id, profile, created_ts}
        self.shipment_by_order = {}  # order_id -> shipment_id
        self.notifications = {}    # order_id -> [{status, reason, ts}]

    def now(self):
        """Секунды от последнего reset (относительное время журнала)."""
        return round(time.monotonic() - self.reset_mono, 3)

    def next_attempt(self, key, order_id):
        k = (key, order_id)
        self.attempts[k] = self.attempts.get(k, 0) + 1
        return self.attempts[k]

    def attempt_count(self, key, order_id):
        return self.attempts.get((key, order_id), 0)

    def log(self, service, action, order_id, attempt, http_status, ok, **details):
        self.ledger.append({
            "ts": self.now(),
            "wall": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "service": service,
            "action": action,
            "order_id": order_id,
            "attempt": attempt,
            "http_status": http_status,
            "ok": ok,
            "details": details,
        })


STATE = State()
