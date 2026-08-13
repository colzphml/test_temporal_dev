"""Сценарии заказов — единственный источник правды для моков и чекера.

Поведение привязано к order_id, но решения агентов обязаны обрабатывать любой
заказ универсально: раскладка сценариев по номерам заказов им не сообщается.
"""

ORDERS = [
    {"order_id": "ORD-1001", "customer": "alice", "amount": 49.90,  "currency": "EUR", "items": [{"sku": "BOOK-CLEAN-ARCH", "qty": 1}]},
    {"order_id": "ORD-1002", "customer": "bob",   "amount": 129.00, "currency": "EUR", "items": [{"sku": "KEYBOARD-K3", "qty": 1}]},
    {"order_id": "ORD-1003", "customer": "carol", "amount": 19.99,  "currency": "EUR", "items": [{"sku": "MUG-TEMPORAL", "qty": 2}]},
    {"order_id": "ORD-1004", "customer": "dave",  "amount": 240.50, "currency": "EUR", "items": [{"sku": "MONITOR-ARM", "qty": 1}]},
    {"order_id": "ORD-1005", "customer": "erin",  "amount": 75.00,  "currency": "EUR", "items": [{"sku": "HEADSET-H1", "qty": 1}]},
    {"order_id": "ORD-1006", "customer": "frank", "amount": 310.00, "currency": "EUR", "items": [{"sku": "CHAIR-ERGO", "qty": 1}]},
    {"order_id": "ORD-1007", "customer": "grace", "amount": 55.40,  "currency": "EUR", "items": [{"sku": "LAMP-LED", "qty": 3}]},
    {"order_id": "ORD-1008", "customer": "heidi", "amount": 89.90,  "currency": "EUR", "items": [{"sku": "BACKPACK-15", "qty": 1}]},
    {"order_id": "ORD-1009", "customer": "ivan",  "amount": 15.00,  "currency": "EUR", "items": [{"sku": "CABLE-USBC", "qty": 5}]},
    {"order_id": "ORD-1010", "customer": "judy",  "amount": 199.00, "currency": "EUR", "items": [{"sku": "SPEAKER-BT", "qty": 1}]},
]

ORDER_IDS = {o["order_id"] for o in ORDERS}
ORDER_BY_ID = {o["order_id"]: o for o in ORDERS}

# Ключи поведения (отсутствие ключа = всё успешно с первой попытки):
#   reserve_500_first: N     — первые N попыток reserve отвечают 500
#   stock: False             — резерв невозможен, всегда 409 out_of_stock
#   charge: "declined"       — оплата всегда 402 card_declined
#   charge_500_first: N      — первые N попыток charge отвечают 500
#   charge_500_mode:
#       "transient" — 500 без побочного эффекта
#       "recorded"  — списание УСПЕВАЕТ записаться, потом 500
#                     (ловушка идемпотентности: ретрай с новым ключом = двойное списание)
#   shipping: "normal" | "slow" | "failed"
#   notify_500_first: N      — первые N попыток notify отвечают 500
SCENARIOS = {
    "ORD-1001": {},
    "ORD-1002": {},
    "ORD-1003": {"reserve_500_first": 1},
    "ORD-1004": {"charge_500_first": 1, "charge_500_mode": "transient"},
    "ORD-1005": {"charge_500_first": 1, "charge_500_mode": "recorded"},
    "ORD-1006": {"charge": "declined"},
    "ORD-1007": {"stock": False},
    "ORD-1008": {"shipping": "failed"},
    "ORD-1009": {"notify_500_first": 2},
    "ORD-1010": {"shipping": "slow"},
}

# Тайминги доставки: (граница в секундах от создания отправления, статус до границы).
# После последней границы: "failed" для профиля failed, иначе "delivered".
SHIPPING_TIMELINE = {
    "normal": [(2.0, "preparing"), (6.0, "shipped")],
    "slow":   [(5.0, "preparing"), (15.0, "shipped")],
    "failed": [(3.0, "preparing")],
}


def shipping_status(profile, elapsed):
    for bound, status in SHIPPING_TIMELINE[profile]:
        if elapsed < bound:
            return status
    return "failed" if profile == "failed" else "delivered"


def scenario(order_id):
    return SCENARIOS.get(order_id, {})
