# Scheduled Run 并发处理方案

> 目标:让计划任务在“上一次 scheduled run 还没跑完、下一次触发已经到点”时表现得更优雅。
> 当前代码会直接跳过这次触发并记录 warning。这个行为安全,但用户看不到丢了几次触发,也没有补偿。

## 1. 当前行为

相关代码:

- `app/scheduler_thread.py`:前端/API 进程内 scheduler。
- `app/tasks.py`:runner 子进程管理与并发槽位。
- `app/schedule_store.py`:计划任务配置持久化到 `~/.tradingagents/app/schedule.json`。

当前并发模型是两层:

```text
run-level:
  scheduled run 最多 1 个同时运行
  manual run 最多 2 个同时运行

ticker-level:
  单个 run 内部通过 `python -m app.runner -j N` 并行跑多个 ticker
```

`app/tasks.py` 中的上限:

```python
MAX_PER_KIND = {"manual": 2, "scheduled": 1}
```

当 scheduler 到点触发时,`scheduler_thread._fire_scheduled_run()` 会调用:

```python
tasks.start_run(..., kind="scheduled")
```

如果上一个 scheduled run 还活着,`tasks.start_run()` 抛 `CapacityExceeded`,目前只是:

```python
logger.warning("Scheduled fire skipped — %s", exc)
```

所以当前行为:

```text
16:30 run A 启动
17:30 到点,run A 仍在跑
17:30 这次触发被 skip,不会排队,不会补跑
```

## 2. 推荐策略:single-flight + coalesced catch-up

保持 scheduled run **single-flight**:

```text
同一时间最多只有一个 scheduled run
```

但不静默丢掉重叠触发。遇到 slot 满时,记录一个“待补跑”状态:

```text
pending_catch_up=true
missed_count += 1
last_missed_at=<iso time>
```

等当前 scheduled run 结束后,如果计划任务仍然 enabled 且 `pending_catch_up=true`,立刻补跑一次,然后清空 pending。

关键点:无论中间错过多少次,只保留 **一个** catch-up。

```text
16:30 run A 启动
17:30 到点,A 未结束 -> pending_catch_up=true, missed_count=1
18:30 到点,A 仍未结束 -> pending_catch_up=true, missed_count=2
19:05 A 结束 -> 立刻启动 run B(catch-up),清空 pending
```

这样避免:

- 多个 scheduled run 重叠打爆 Gonka / yfinance / SQLite。
- 错过触发静默消失。
- 无限队列积压。

## 3. 功能目标

这不是“少做功能”。目标是完整处理 scheduled run 的运行语义,但不把系统升级成 Celery / Redis / 数据库队列。

需要覆盖的能力:

- **single-flight**:同一时间最多一个 scheduled run。
- **coalesced catch-up**:重叠触发不丢失,但合并成最多一个补跑。
- **状态可见**:UI/API 能看到 pending、missed 次数、最近错过时间、最近补跑时间。
- **失败不误清**:catch-up 启动失败时 pending 不应被清空。
- **重启可恢复**:FastAPI 重启后仍能从 `schedule.json` 恢复 pending 状态。
- **暂停语义明确**:Pause 后不补跑;重新启用后是否补跑由配置策略决定。
- **不影响 manual run**:manual 仍可独立运行,但 scheduled slot 只服务计划任务。
- **不制造无限队列**:不管错过多少次,只保留一个待补跑批次。

## 4. 数据模型

在 `schedule.json` 中保留配置字段,新增运行状态字段。最终形状:

```json
{
  "enabled": true,
  "start_hour": 16,
  "start_minute": 30,
  "interval_hours": 24,
  "workers": 4,
  "tickers": ["NVDA", "AAPL"],

  "pending_catch_up": false,
  "missed_count": 0,
  "last_missed_at": null,
  "last_catch_up_at": null,
  "last_regular_fire_at": null,
  "last_start_error": null,
  "pause_clears_pending": false
}
```

字段语义:

| 字段 | 语义 |
| --- | --- |
| `pending_catch_up` | 是否有一个合并后的补跑等待启动。 |
| `missed_count` | 当前 pending 周期内错过了多少次触发。补跑成功启动后清零。 |
| `last_missed_at` | 最近一次因为 scheduled slot 满而推迟的时间。 |
| `last_catch_up_at` | 最近一次 catch-up 成功启动的时间。 |
| `last_regular_fire_at` | 最近一次常规定时触发时间,用于 UI 和排障。 |
| `last_start_error` | 最近一次启动失败原因,例如 credentials 缺失或 subprocess 启动异常。成功启动后清空。 |
| `pause_clears_pending` | 可选策略开关。默认 `false`:Pause 只停止触发,不清 pending。 |

说明:`schedule.json` 不是高并发数据源,但这里会有 API 写入、scheduler 写入、catch-up watcher 写入。建议 `schedule_store.py` 加模块级 `RLock`,保证 load-modify-save 原子。

## 5. 实现改动

### 5.1 `schedule_store.py`:状态 helper

在 `_defaults()` 增加:

```python
"pending_catch_up": False,
"missed_count": 0,
"last_missed_at": None,
"last_catch_up_at": None,
"last_regular_fire_at": None,
"last_start_error": None,
"pause_clears_pending": False,
```

新增 helper:

```python
_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def update_schedule(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    with _LOCK:
        cfg = load_schedule()
        mutator(cfg)
        save_schedule(cfg)
        return cfg


def mark_regular_fire() -> dict[str, Any]:
    return update_schedule(lambda cfg: cfg.update({
        "last_regular_fire_at": _now_iso(),
        "last_start_error": None,
    }))


def mark_missed_fire() -> dict[str, Any]:
    def mutate(cfg):
        cfg["pending_catch_up"] = True
        cfg["missed_count"] = int(cfg.get("missed_count") or 0) + 1
        cfg["last_missed_at"] = _now_iso()
        cfg["last_start_error"] = None
    return update_schedule(mutate)


def consume_pending_catch_up() -> dict[str, Any]:
    def mutate(cfg):
        cfg["pending_catch_up"] = False
        cfg["missed_count"] = 0
        cfg["last_catch_up_at"] = _now_iso()
        cfg["last_start_error"] = None
    return update_schedule(mutate)


def mark_start_error(message: str) -> dict[str, Any]:
    return update_schedule(lambda cfg: cfg.update({
        "last_start_error": message,
    }))
```

`save_schedule()` 保持 temp-file + atomic rename。

### 5.2 `scheduler_thread.py`:拆出启动函数

把“读取配置、校验 credentials、组装 tickers/workers、调用 `tasks.start_run`”从 `_fire_scheduled_run()` 拆成 `_start_scheduled_run(reason)`。

语义:

```python
def _start_scheduled_run(reason: Literal["regular", "catch-up"]) -> bool:
    cfg = schedule_store.load_schedule()
    if not cfg.get("enabled"):
        return False

    settings = load_settings()
    if not mode_is_configured(settings):
        schedule_store.mark_start_error("missing credentials")
        logger.warning("Scheduled %s skipped — missing credentials", reason)
        return False

    tickers = ...
    workers = ...

    task = tasks.start_run(
        settings=settings,
        tickers=tickers,
        workers=workers,
        kind="scheduled",
    )
    logger.info("Scheduled %s run started: PID %d", reason, task["pid"])
    return True
```

`CapacityExceeded` 要继续抛出,因为常规触发和 catch-up watcher 需要不同处理。

### 5.3 常规触发:slot 满则 pending

```python
def _fire_scheduled_run() -> None:
    schedule_store.mark_regular_fire()
    try:
        _start_scheduled_run(reason="regular")
    except tasks.CapacityExceeded as exc:
        cfg = schedule_store.mark_missed_fire()
        logger.warning(
            "Scheduled fire deferred — %s; missed_count=%s",
            exc,
            cfg.get("missed_count"),
        )
    except Exception as exc:
        schedule_store.mark_start_error(str(exc))
        logger.exception("Scheduled regular run failed")
```

### 5.4 Catch-up watcher

新增 job id:

```python
_CATCH_UP_JOB_ID = "tradingagents-scheduled-catch-up"
```

在 `_get_scheduler()` 初始化时注册一个 interval job,每 30 或 60 秒检查一次:

```python
sched.add_job(
    _maybe_start_catch_up,
    trigger=IntervalTrigger(seconds=60),
    id=_CATCH_UP_JOB_ID,
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
```

实现:

```python
def _maybe_start_catch_up() -> None:
    cfg = schedule_store.load_schedule()
    if not cfg.get("enabled") or not cfg.get("pending_catch_up"):
        return

    counts = tasks.count_active_by_kind()
    if counts.get("scheduled", 0) > 0:
        return

    try:
        started = _start_scheduled_run(reason="catch-up")
    except tasks.CapacityExceeded:
        return
    except Exception as exc:
        schedule_store.mark_start_error(str(exc))
        logger.exception("Scheduled catch-up failed")
        return

    if started:
        schedule_store.consume_pending_catch_up()
```

这个 watcher 的好处:

- 不需要 `tasks.py` 支持子进程完成回调。
- 不需要引入任务队列。
- API server 重启后仍可从 `schedule.json` 读到 pending 状态并继续补跑。

缺点:

- catch-up 最多延迟 30-60 秒启动。
- watcher 是轮询,不是子进程完成事件回调。但实现简单,重启恢复自然。

### 5.5 Pause / Save 语义

前端点击 Pause 时会 `PUT /api/schedule` 且 `enabled=false`。

推荐语义:

- Pause 不补跑。
- 默认不清 pending,因为 Pause 可能只是临时停止。
- 如果希望 Pause 等于取消 backlog,设置 `pause_clears_pending=true` 或前端提供 “Pause & clear pending”。

后端 `put_schedule()` 可以在保存前处理:

```python
if not cfg["enabled"] and cfg.get("pause_clears_pending"):
    cfg["pending_catch_up"] = False
    cfg["missed_count"] = 0
```

### 5.6 `tasks.py`:不需要改并发模型

保持:

```python
MAX_PER_KIND = {"manual": 2, "scheduled": 1}
```

这是整个方案的核心保护。catch-up 只是下一次 scheduled run 的启动时机,不是增加并发。

## 6. API 和前端展示

`app/api.py` 的 `_schedule_payload()` 已经返回 `config`,所以新增字段会自动出现在:

```http
GET /api/schedule
```

前端 `frontend/pages/schedule.vue` 应展示:

```text
Pending catch-up: Yes/No
Missed fires: N
Last missed: ...
Last catch-up started: ...
Last regular fire: ...
Last start error: ...
```

建议 UI 文案:

```text
One scheduled run is already active. Missed fires are coalesced into one catch-up run.
```

卡片/状态建议:

- Schedule status: Enabled / Disabled
- Next regular fire: APScheduler `next_run`
- Pending catch-up: Yes / No
- Missed fires: `missed_count`
- Active scheduled slot: `capacity.scheduled.current / capacity.scheduled.max`

按钮建议:

- Save & activate
- Pause schedule
- Clear pending catch-up:仅当 `pending_catch_up=true` 时显示,调用 `PUT /api/schedule` 清空 pending。

## 7. 为什么不直接增加 scheduled 并发

如果允许多个 scheduled run 并行,实际并发会被乘起来:

```text
2 个 scheduled run * 每个 -j 4 = 8 个 ticker 同时分析
```

再叠加 manual run,会给以下资源带来压力:

- Gonka gateway / router rate limit。
- yfinance / 新闻数据源限流。
- SQLite 写入与日志文件。
- 本机 CPU / 内存。

已有的 `-j N` ticker-level 并发已经覆盖了单次批处理提速需求。run-level 继续保持 single-flight 更稳。

## 8. 验收用例

1. **正常触发**
   - schedule enabled。
   - 没有 active scheduled task。
   - 到点后启动一个 `kind="scheduled"` task。
   - `pending_catch_up=false`。
   - `last_regular_fire_at` 更新。
   - `last_start_error=null`。

2. **重叠触发**
   - 手动制造一个长时间 scheduled task。
   - scheduler 到点。
   - 不启动第二个 scheduled task。
   - `pending_catch_up=true`, `missed_count=1`。
   - `last_missed_at` 更新。

3. **合并多次错过**
   - 长任务期间触发多次。
   - `missed_count` 增加。
   - active scheduled task 仍然只有 1 个。
   - 长任务结束后只补跑 1 次。
   - 补跑成功启动后 `pending_catch_up=false`, `missed_count=0`, `last_catch_up_at` 更新。

4. **禁用计划任务**
   - `pending_catch_up=true` 时点击 Pause。
   - watcher 不补跑。
   - 默认 pending 保留但不会执行。
   - 如果用户点击 Clear pending catch-up,则 `pending_catch_up=false`, `missed_count=0`。

5. **API server 重启**
   - `pending_catch_up=true` 写在 `schedule.json`。
   - 重启 `uvicorn app.api:app`。
   - scheduler startup reload 后 watcher 继续观察。
   - 无 active scheduled task 时补跑一次。

6. **Catch-up 启动失败**
   - `pending_catch_up=true`。
   - 清空 credentials 或让 settings invalid。
   - watcher 尝试 catch-up。
   - 不清 pending。
   - `last_start_error` 写入。

7. **Manual run 不阻塞 scheduled catch-up**
   - manual 已有 2 个 active。
   - scheduled slot 空,`pending_catch_up=true`。
   - watcher 仍可启动 scheduled catch-up,因为 manual/scheduled slot 分开计算。

8. **Scheduled run 不叠加**
   - active scheduled=1,`pending_catch_up=true`。
   - watcher 检查时不启动新 run。
   - active scheduled 仍为 1。

## 9. 推荐结论

采用 **single-flight + coalesced catch-up**。

这是当前项目功能完整、同时架构改动克制的方案:

- 保留 scheduled run 并发上限 1。
- 利用现有 `tasks.start_run()` 和 `kind="scheduled"`。
- 扩展 `schedule.json` 状态、`scheduler_thread.py` 触发逻辑、schedule 页面状态展示。
- catch-up 启动失败不清 pending,可被用户看见并修复。
- API server 重启后 pending 状态可恢复。
- 不引入队列、数据库迁移或复杂 worker。
