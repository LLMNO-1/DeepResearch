"""后台任务数据访问层。

按任务生命周期顺序排列，从上到下即完整执行链路：
  创建(QUEUED) → 查询(轮询) → 执行中(RUNNING) → 结束(SUCCEEDED/FAILED)

内部辅助函数统一放在文件末尾。
"""
from datetime import datetime

from app.repository.mongodb import get_mongodb_database
from app.schemas import TaskStatus, TaskStatusResponse, TaskType, utc_now

COLLECTION_NAME = "research_tasks"


# =============================================================================
# 1. 任务创建 —— 路由层提交后台任务时调用
# =============================================================================

async def create_task(
    task_id: str,
    project_id: str,
    task_type: TaskType,
    status: TaskStatus,
    message: str,
    created_at: datetime,
    updated_at: datetime,
) -> TaskStatusResponse:
    """创建后台任务记录，初始状态为 QUEUED。

    调用方：routers/__init__.py _create_task()
    时机：POST /research-projects、PUT /outline (revise)、POST /report-tasks 三个入口
    注意：该函数只写 MongoDB，不启动任何后台任务。后台任务由 background 模块通过asyncio.create_task 提交。
    """

    task = TaskStatusResponse(
        task_id=task_id,
        project_id=project_id,
        task_type=task_type,
        status=status,
        message=message,
        created_at=created_at,
        updated_at=updated_at,
    )
    # model_dump 把 Pydantic 对象递归转为 Python 原生类型
    # 例如 TaskType.GENERATE_RESEARCH_BRIEF → "generate_research_brief"，datetime → datetime 对象
    # 纯内存操作，无 I/O
    document = task.model_dump(mode="python")
    document["_id"] = task_id
    # insert_one 是 pymongo 异步驱动的核心 I/O：
    # 1. 从连接池取连接到 MongoDB
    # 2. 对 deep_research.research_tasks 集合执行 insert_one
    # 3. 服务端写入文档，返回 InsertOneResult
    await _get_collection().insert_one(document)
    return task


# =============================================================================
# 2. 任务查询 —— 前端每 2 秒轮询一次
# =============================================================================

async def get_task(task_id: str) -> TaskStatusResponse | None:
    """根据 task_id 读取任务状态。

    调用方：routers/__init__.py get_task() → GET /tasks/{task_id}
    时机：前端轮询，每 2 秒一次
    任务不存在时返回 None，路由层转为 404。
    """

    document = await _get_collection().find_one({"task_id": task_id})
    return _task_from_document(document)


# =============================================================================
# 3. 状态变更 —— 后台任务执行过程中三次状态切换
#    QUEUED → RUNNING → SUCCEEDED / FAILED
# =============================================================================

async def mark_task_running(task_id: str, message: str) -> None:
    """标记任务为执行中。

    调用方：background/research_tasks.py 四个后台任务的入口
    时机：asyncio.create_task 启动的协程开始执行后立即可调用
    """

    await _update_task_status(task_id=task_id, status=TaskStatus.RUNNING, message=message)


async def mark_task_succeeded(task_id: str, message: str) -> None:
    """标记任务为执行成功。

    调用方：background/research_tasks.py 四个后台任务的 try 分支末尾
    时机：Agent 调用完成、结果写库完成之后
    """

    await _update_task_status(task_id=task_id, status=TaskStatus.SUCCEEDED, message=message)


async def mark_task_failed(task_id: str, message: str) -> None:
    """标记任务为执行失败。

    调用方：background/research_tasks.py _mark_task_failed()
    时机：后台任务捕获异常后调用
    message 只保存错误摘要，不保存 API Key、用户隐私原文等敏感信息。
    """

    await _update_task_status(task_id=task_id, status=TaskStatus.FAILED, message=message)


# =============================================================================
# 内部辅助函数
# =============================================================================

def _get_collection():
    """获取 MongoDB research_tasks 集合对象。"""
    return get_mongodb_database()[COLLECTION_NAME]


def _task_from_document(document: dict[str, object] | None) -> TaskStatusResponse | None:
    """MongoDB 文档 → TaskStatusResponse。

    status 和 task_type 字段在 MongoDB 中是字符串，需要转回对应的 StrEnum。
    created_at / updated_at 是 MongoDB 写入的 datetime 对象，直接赋值，不做转换。
    """

    if document is None:
        return None
    return TaskStatusResponse(
        task_id=str(document["task_id"]),
        project_id=str(document["project_id"]),
        task_type=TaskType(str(document["task_type"])),
        status=TaskStatus(str(document["status"])),
        message=str(document["message"]),
        created_at=document["created_at"],  # type: ignore[arg-type]
        updated_at=document["updated_at"],  # type: ignore[arg-type]
    )


async def _update_task_status(task_id: str, status: TaskStatus, message: str) -> None:
    """统一的状态更新底层实现，同时更新 status、message、updated_at。

    mark_task_running / mark_task_succeeded / mark_task_failed 三个方法都委托此函数，
    避免重复拼写 update_one 的 $set 字段。
    """

    await _get_collection().update_one(
        {"task_id": task_id},
        {
            "$set": {
                "status": status,
                "message": message,
                "updated_at": utc_now(),
            }
        },
    )


"""
 ┌─────────────────────┬──────────────────────────────────┬──────────────────────┐
  │        方法         │              调用方              │       调用次数       │
  ├─────────────────────┼──────────────────────────────────┼──────────────────────┤
  │ create_task         │ routers _create_task()           │ 1 处（3 个入口共用） │
  ├─────────────────────┼──────────────────────────────────┼──────────────────────┤
  │ get_task            │ routers GET /tasks/{id}          │ 1 处                 │
  ├─────────────────────┼──────────────────────────────────┼──────────────────────┤
  │ mark_task_running   │ background 四个后台任务入口      │ 4 处                 │
  ├─────────────────────┼──────────────────────────────────┼──────────────────────┤
  │ mark_task_succeeded │ background 四个后台任务 try 分支 │ 4 处                 │
  ├─────────────────────┼──────────────────────────────────┼──────────────────────┤
  │ mark_task_failed    │ background _mark_task_failed()   │ 1 处（4 个任务共用） │
  └─────────────────────┴──────────────────────────────────┴──────────────────────┘

  3 个内部辅助：_get_collection、_task_from_document、_update_task_status。
"""