# Фаза 2, вариант Temporal: как пережить SIGKILL

Состояние workflow'ов живёт на сервере Temporal и переживает смерть worker'а
само — в этом суть durable-исполнения. Убивают только ТВОЙ процесс (worker +
starter), поэтому меняется в основном starter:

1. **Workflow id — детерминированный в пределах прогона**:
   `id=f"order-{oid}-{os.environ['RUN_ID']}"`. Никаких `time.time()` или uuid
   в starter: повторный запуск обязан попадать в ТЕ ЖЕ workflow'ы.
2. **Повторный starter подцепляется к существующим workflow'ам**, а не заводит
   новые:

```python
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

async def run_or_attach(client, order, run_id):
    wf_id = f"order-{order['order_id']}-{run_id}"
    try:
        handle = await client.start_workflow(
            OrderWorkflow.run, order, id=wf_id, task_queue="orders",
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(wf_id)  # уже идёт или завершён — цепляемся
    return await handle.result()
```

3. **Idempotency-Key генерируй внутри workflow** через `workflow.uuid4()`, как
   в фазе 1: при реплее истории он детерминированно восстановится — рестарт
   worker'а его не меняет. (Ключ, сгенерированный в starter или в activity,
   этого свойства не имеет.)
4. Worker в повторном запуске поднимается как обычно (тот же run.sh-паттерн) и
   продолжает исполнение застрявших activity.

## Чеклист фазы 2

- [ ] Workflow id строится из RUN_ID; time.time()/uuid в starter отсутствуют.
- [ ] start_workflow с REJECT_DUPLICATE + get_workflow_handle в except.
- [ ] Ключ оплаты — workflow.uuid4() внутри workflow.
- [ ] Все инварианты фазы 1 по-прежнему выполняются.
