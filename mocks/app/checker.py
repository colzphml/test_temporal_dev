"""Чекер инвариантов: судья эксперимента.

Проверяет журнал моков после прогона решения. Все проверки соответствуют
SPEC.md один в один — ничего сверх спецификации здесь не требуется.
"""
from .scenarios import ORDERS, scenario
from .state import STATE

TIME_BUDGET = 90.0   # мягкий лимит на весь прогон (санити, не гонка со стеком)
MIN_CONCURRENT = 8   # в пике столько заказов должно быть в работе одновременно


def _expected_final(scen):
    if scen.get("stock", True) is False:
        return "cancelled"
    if scen.get("charge") == "declined":
        return "cancelled"
    if scen.get("shipping", "normal") == "failed":
        return "cancelled"
    return "completed"


def _events(oid):
    return [e for e in STATE.ledger if e["order_id"] == oid]


def _first(evs, service, action, ok=True, pred=None):
    for e in evs:  # ledger уже в хронологическом порядке
        if e["service"] == service and e["action"] == action and e["ok"] == ok:
            if pred is None or pred(e):
                return e
    return None


class OrderCheck:
    def __init__(self, order):
        self.order = order
        self.oid = order["order_id"]
        self.problems = []
        self.warnings = []

    def fail(self, msg, hint=None):
        self.problems.append(msg + (f"  → {hint}" if hint else ""))

    def warn(self, msg):
        self.warnings.append(msg)


def _check_order(order):  # noqa: C901 — линейный сценарий проверок читабельнее раздробленного
    c = OrderCheck(order)
    oid = c.oid
    scen = scenario(oid)
    final = _expected_final(scen)
    evs = _events(oid)

    if not evs:
        c.fail("заказ вообще не обрабатывался: ни одного вызова API",
               "решение должно получить список заказов из GET /api/orders и обработать каждый")
        return c, final

    res = STATE.reservations.get(oid)
    charges = STATE.charges.get(oid, [])
    refunds = STATE.refunds.get(oid, [])
    sid = STATE.shipment_by_order.get(oid)
    notes = STATE.notifications.get(oid, [])
    final_note = notes[-1] if notes else None

    reserve_ok = _first(evs, "inventory", "reserve")
    release_ok = _first(evs, "inventory", "release",
                        pred=lambda e: e["details"].get("result") == "released")
    charge_ok = _first(evs, "payments", "charge")
    refund_ok = _first(evs, "payments", "refund")
    ship_created = _first(evs, "shipping", "create_shipment")
    delivered_poll = _first(evs, "shipping", "poll",
                            pred=lambda e: e["details"].get("status") == "delivered")
    failed_poll = _first(evs, "shipping", "poll",
                         pred=lambda e: e["details"].get("status") == "failed")

    # --- финальное уведомление: обязательно для любого исхода
    if final_note is None:
        c.fail("нет ни одного успешного уведомления (POST /api/notifications/notify)",
               "каждый заказ обязан закончиться уведомлением completed или cancelled; 500 нужно ретраить")
    elif final_note["status"] != final:
        c.fail(f"последнее уведомление имеет статус '{final_note['status']}', ожидался '{final}'")
    wrong_notes = [nt for nt in notes if nt["status"] != final]
    if wrong_notes:
        c.fail(f"отправлены противоречивые уведомления: {[nt['status'] for nt in notes]}")

    note_ok = None
    if final_note is not None and final_note["status"] == final:
        note_ok = _first(evs, "notifications", "notify",
                         pred=lambda e: e["details"].get("status") == final)

    def in_order(pairs):
        """Проверка порядка шагов: список (событие, имя); события обязаны идти по возрастанию ts."""
        prev_e, prev_name = None, None
        for e, name in pairs:
            if e is None:
                continue  # отсутствие уже зафиксировано отдельной проверкой
            if prev_e is not None and e["ts"] < prev_e["ts"]:
                c.fail(f"нарушен порядок шагов: '{name}' (t={e['ts']}) раньше, чем '{prev_name}' (t={prev_e['ts']})",
                       "см. порядок шагов в SPEC.md")
            prev_e, prev_name = e, name

    # --- ретраи там, где сценарий подбрасывал 500
    need = scen.get("reserve_500_first", 0) + 1
    if scen.get("stock", True) and STATE.attempt_count("inventory.reserve", oid) < need:
        c.fail(f"reserve вызван {STATE.attempt_count('inventory.reserve', oid)} раз(а), сервис отвечал 500 — нужно минимум {need}",
               "5xx нужно ретраить с бэкоффом (SPEC.md, раздел «Ошибки и ретраи»)")
    if scen.get("charge_500_first", 0) and scen.get("charge") != "declined":
        need = scen["charge_500_first"] + 1
        if STATE.attempt_count("payments.charge", oid) < need:
            c.fail(f"charge вызван {STATE.attempt_count('payments.charge', oid)} раз(а) при временных 500 — нужно минимум {need}",
                   "5xx нужно ретраить с тем же Idempotency-Key")
    if scen.get("notify_500_first", 0):
        need = scen["notify_500_first"] + 1
        if STATE.attempt_count("notifications.notify", oid) < need:
            c.fail(f"notify вызван {STATE.attempt_count('notifications.notify', oid)} раз(а) при временных 500 — нужно минимум {need}",
                   "уведомление — обязательный шаг, его тоже нужно ретраить")

    # --- проверки по ожидаемому пути
    if final == "completed":
        if res is None:
            c.fail("нет резерва на складе")
        elif res["released"]:
            c.fail("резерв снят (release), хотя заказ успешно завершён")
        if len(charges) == 0:
            c.fail("нет успешного списания оплаты")
        elif len(charges) > 1:
            c.fail(f"ДВОЙНОЕ СПИСАНИЕ: {len(charges)} записанных списаний",
                   "ретрай charge обязан идти с тем же Idempotency-Key (SPEC.md, раздел «Идемпотентность оплаты»)")
        else:
            if abs(charges[0]["amount"] - order["amount"]) > 0.005:
                c.fail(f"списана неверная сумма {charges[0]['amount']}, ожидалась {order['amount']}")
            if not charges[0]["key"]:
                c.warn("charge выполнен без заголовка Idempotency-Key — сработает не всегда")
        if refunds:
            c.fail("по успешному заказу сделан refund")
        if sid is None:
            c.fail("не создано отправление (POST /api/shipping/shipments)")
        if delivered_poll is None:
            c.fail("решение не дождалось статуса 'delivered' (нет опроса, увидевшего delivered)",
                   "опрашивай GET /api/shipping/shipments/{id} раз в секунду до терминального статуса")
        in_order([(reserve_ok, "reserve"), (charge_ok, "charge"), (ship_created, "создание отправления"),
                  (delivered_poll, "доставка delivered"), (note_ok, "уведомление completed")])

    elif scen.get("stock", True) is False:  # нет на складе
        if res is not None:
            c.fail("резерв создан, хотя склад отвечает 409 out_of_stock")
        if STATE.attempt_count("payments.charge", oid) > 0:
            c.fail("вызывалась оплата, хотя резерв не удался",
                   "при 409 out_of_stock заказ сразу отменяется без оплаты")
        if charges or refunds:
            c.fail("есть списания/возвраты по заказу без резерва")
        if sid is not None:
            c.fail("создано отправление по отменённому заказу")
        reserve_409 = _first(evs, "inventory", "reserve", ok=False,
                             pred=lambda e: e["http_status"] == 409)
        in_order([(reserve_409, "reserve 409"), (note_ok, "уведомление cancelled")])

    elif scen.get("charge") == "declined":
        if res is None:
            c.fail("нет резерва на складе")
        elif not res["released"]:
            c.fail("резерв не снят после отклонения оплаты",
                   "компенсация: при 402 нужно вызвать POST /api/inventory/release")
        if charges:
            c.fail("записано успешное списание, хотя банк отвечает 402")
        if STATE.attempt_count("payments.charge", oid) == 0:
            c.fail("оплата не вызывалась")
        if STATE.attempt_count("payments.charge", oid) > 1:
            c.warn("402 card_declined — постоянная ошибка, ретраить её не нужно")
        if refunds:
            c.fail("refund по заказу без успешного списания")
        if sid is not None:
            c.fail("создано отправление по отменённому заказу")
        charge_402 = _first(evs, "payments", "charge", ok=False,
                            pred=lambda e: e["http_status"] == 402)
        in_order([(reserve_ok, "reserve"), (charge_402, "charge 402"),
                  (release_ok, "release"), (note_ok, "уведомление cancelled")])

    else:  # shipping failed
        if res is None:
            c.fail("нет резерва на складе")
        elif not res["released"]:
            c.fail("резерв не снят после провала доставки",
                   "компенсация: release + refund + уведомление cancelled")
        if len(charges) != 1:
            c.fail(f"ожидалось ровно 1 списание, записано {len(charges)}")
        if len(refunds) == 0:
            c.fail("нет возврата денег (refund) после провала доставки",
                   "компенсация: при статусе 'failed' нужно вызвать POST /api/payments/refund")
        elif len(refunds) > 1:
            c.fail(f"сделано {len(refunds)} возвратов, ожидался 1")
        elif charges and abs(refunds[0]["amount"] - charges[0]["amount"]) > 0.005:
            c.fail("сумма возврата не равна сумме списания")
        if sid is None:
            c.fail("отправление не создавалось")
        if failed_poll is None:
            c.fail("решение не увидело статус 'failed' у отправления (нет такого опроса)")
        in_order([(reserve_ok, "reserve"), (charge_ok, "charge"), (ship_created, "создание отправления"),
                  (failed_poll, "доставка failed"), (refund_ok, "refund"), (note_ok, "уведомление cancelled")])
        in_order([(failed_poll, "доставка failed"), (release_ok, "release"), (note_ok, "уведомление cancelled")])

    return c, final


def run_checks():
    lines = []
    all_ok = True

    if not STATE.ledger:
        return False, ("❌ Журнал пуст: после сброса не было ни одного вызова API.\n"
                       "   Решение не запускалось или ходит не на тот адрес (ожидается http://localhost:8100).")

    known = {o["order_id"] for o in ORDERS}
    alien = sorted({e["order_id"] for e in STATE.ledger
                    if e["order_id"] is not None and e["order_id"] not in known})
    if alien:
        all_ok = False
        lines.append(f"❌ Вызовы API с несуществующими order_id: {alien}")

    intervals = []  # (t_start, t_end) обработки каждого заказа
    for order in ORDERS:
        c, final = _check_order(order)
        if c.problems:
            all_ok = False
            lines.append(f"❌ {c.oid} (ожидался исход: {final})")
            for p in c.problems:
                lines.append(f"     • {p}")
        else:
            lines.append(f"✅ {c.oid} → {final}")
        for w in c.warnings:
            lines.append(f"     ⚠ {w}")
        evs = _events(c.oid)
        note_evs = [e for e in evs
                    if e["service"] == "notifications" and e["ok"]
                    and e["details"].get("status") == final]
        if evs and note_evs:
            intervals.append((evs[0]["ts"], note_evs[0]["ts"]))

    # --- фаза 2: разрыв (SIGKILL + рестарт) должен быть пережит
    if STATE.kill_ts is not None:
        pre_charges = [e for e in STATE.ledger
                       if e["service"] == "payments" and e["action"] == "charge"
                       and e["ok"] and e["ts"] < STATE.kill_ts]
        post_notes = [e for e in STATE.ledger
                      if e["service"] == "notifications" and e["ok"]
                      and e["ts"] > STATE.kill_ts]
        if not pre_charges:
            all_ok = False
            lines.append(f"❌ До SIGKILL (t={STATE.kill_ts:.1f}с) не было ни одного успешного "
                         "списания — разрыв случился до начала реальной работы")
        if not post_notes:
            all_ok = False
            lines.append(f"❌ После SIGKILL (t={STATE.kill_ts:.1f}с) и рестарта не отправлено "
                         "ни одного уведомления — решение не пережило разрыв "
                         "(повторный запуск run.sh обязан довести заказы до конца)")
        if pre_charges and post_notes:
            lines.append(f"💀 SIGKILL на t={STATE.kill_ts:.1f}с пережит: списаний до разрыва "
                         f"{len(pre_charges)}, уведомлений после рестарта {len(post_notes)}")

    # --- конкурентность и общий лимит времени
    t0 = STATE.ledger[0]["ts"]
    if len(intervals) == len(ORDERS):
        elapsed = max(e for _, e in intervals) - t0
        marks = []
        for s, e in intervals:
            marks.append((s, 1))
            marks.append((e, -1))
        marks.sort(key=lambda m: (m[0], m[1]))  # при равенстве времени: сначала закрытие
        cur = peak = 0
        for _, d in marks:
            cur += d
            peak = max(peak, cur)
        if peak < MIN_CONCURRENT:
            all_ok = False
            lines.append(f"❌ Заказы обрабатывались почти последовательно: в пике одновременно "
                         f"{peak} из {len(ORDERS)} — запускай обработку всех заказов параллельно")
        if elapsed > TIME_BUDGET:
            all_ok = False
            lines.append(f"❌ Общее время обработки {elapsed:.1f}с > лимита {TIME_BUDGET:.0f}с")
        if peak >= MIN_CONCURRENT and elapsed <= TIME_BUDGET:
            lines.append(f"⏱ Все заказы за {elapsed:.1f}с (лимит {TIME_BUDGET:.0f}с); "
                         f"в пике одновременно: {peak}")

    verdict = "✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if all_ok else "❌ ПРОВЕРКИ НЕ ПРОЙДЕНЫ"
    return all_ok, "\n".join(lines + [verdict])
