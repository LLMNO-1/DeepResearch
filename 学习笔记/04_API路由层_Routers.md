# 模块 4：API 路由层 — Routers

## 涉及文件

- `app/routers/__init__.py` —— 6 个 API 端点，约 300 行

---

## 4.1 端点全景

| 方法 | 路径 | 功能 | 状态影响 |
|------|------|------|----------|
| POST | `/research-projects` | 创建研究项目 | CREATED → BRIEF_GENERATING |
| GET | `/research-projects/{id}/outline` | 获取大纲 | 无 |
| PUT | `/research-projects/{id}/outline` | 确认/修改大纲 | OUTLINE_READY → CONFIRMED / REVISING |
| POST | `/research-projects/{id}/report-tasks` | 提交报告生成 | OUTLINE_CONFIRMED → RESEARCH_RUNNING |
| POST | `/research-projects/{id}/report-render-tasks` | 提交独立渲染 | 无 |
| GET | `/tasks/{task_id}` | 查询任务状态 | 无（只读） |
| GET | `/research-projects/{id}/reports/latest` | 获取最新报告 | 无（只读） |

---

## 4.2 路由层的职责定位

路由层只做四件事：
1. **参数校验**（Pydantic 自动完成）
2. **权限/状态检查**（如大纲未确认不能生成报告 → 返回 409）
3. **委托后台**（调用 `start_xxx_task()` 启动异步执行）
4. **返回响应**（立即返回 task_id / 数据）

路由层**不做**：
- 不直接调用 Agent
- 不直接写数据库（除了创建任务记录和更新项目状态）
- 不执行耗时操作

---

## 4.3 两个内部辅助函数

```python
async def _create_task(project_id, task_type, message):
    now = utc_now()
    return await research_task_repository.create_task(
        task_id=str(uuid4()),
        project_id=project_id,
        task_type=task_type,
        status=TaskStatus.QUEUED,
        message=message,
        created_at=now, updated_at=now,
    )

async def _get_project(project_id):
    project = await research_project_repository.get_project(project_id=project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="研究项目不存在")
    return project
```

`_create_task` 封装了任务创建的标准流程——生成 UUID、设置 QUEUED 状态、统一时间戳。`_get_project` 封装了"取项目或 404"的通用模式。

---

## 4.4 逐端点分析

### 4.4.1 创建研究项目

```python
@router.post("/research-projects", response_model=ResearchProjectCreateResponse, status_code=201)
async def create_research_project(request: ResearchProjectCreate):
    project_id = str(uuid4())
    created_at = utc_now()

    # 1. 创建后台任务记录
    task = await _create_task(
        project_id=project_id,
        task_type=TaskType.GENERATE_RESEARCH_BRIEF,
        message="研究任务书和大纲生成任务已创建",
    )

    # 2. 创建项目记录
    await research_project_repository.create_project(
        project_id=project_id, request=request,
        topic=request.topic, status=ProjectStatus.BRIEF_GENERATING,
        created_at=created_at,
    )

    # 3. 启动后台任务（fire-and-forget）
    start_generate_research_brief_task(project_id=project_id, task_id=task.task_id)

    # 4. 立即返回
    return ResearchProjectCreateResponse(
        project_id=project_id,
        initial_task_id=task.task_id,
        next_step=NextStep.WAIT_FOR_OUTLINE,
        # ...
    )
```

关键流程：**创建记录 → 启动后台 → 立即返回**。前端拿到 `task_id` 后开始轮询 `GET /tasks/{task_id}`，同时显示"正在生成大纲..."的 loading 状态。

### 4.4.2 获取大纲

```python
@router.get("/research-projects/{project_id}/outline", response_model=OutlineResponse)
async def get_outline(project_id: str):
    project = await _get_project(project_id)
    outline = await research_project_repository.get_outline(project_id=project_id)
    return OutlineResponse(
        project_id=project_id,
        status=project["status"],
        outline=outline if isinstance(outline, list) else [],
    )
```

纯查询端点，不触发任何后台任务。

### 4.4.3 确认/修改大纲（核心端点）

```python
@router.put("/research-projects/{project_id}/outline",
    response_model=OutlineConfirmResponse | OutlineRevisionResponse)
async def update_outline(project_id: str, request: OutlineUpdateRequest):
    await _get_project(project_id)  # 确保项目存在

    if request.action == OutlineAction.CONFIRM:
        # 确认路径：检查大纲是否存在
        outline = await research_project_repository.get_outline(project_id=project_id)
        if not outline:
            raise HTTPException(status_code=409,
                detail="当前研究项目尚未生成可确认的大纲")

        # 保存确认后的大纲副本
        await research_project_repository.save_confirmed_outline(
            project_id=project_id, outline=outline)

        # 更新项目状态
        await research_project_repository.update_project_status(
            project_id=project_id, status=ProjectStatus.OUTLINE_CONFIRMED)

        return OutlineConfirmResponse(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_CONFIRMED,
            next_step=NextStep.GENERATE_REPORT,  # 前端显示"生成报告"按钮
        )

    # 修改路径：创建修改任务并启动后台
    task = await _create_task(
        project_id=project_id, task_type=TaskType.REVISE_OUTLINE,
        message="大纲修改任务已创建")

    await research_project_repository.update_project_status(
        project_id=project_id, status=ProjectStatus.OUTLINE_REVISING)

    start_revise_outline_task(
        project_id=project_id, task_id=task.task_id,
        revision_instruction=request.revision_instruction or "")

    return OutlineRevisionResponse(
        project_id=project_id,
        revision_task_id=task.task_id,
        status=ProjectStatus.OUTLINE_REVISING,
        next_step=NextStep.WAIT_FOR_OUTLINE,  # 前端重新等待大纲
    )
```

**返回类型用 Union**：`OutlineConfirmResponse | OutlineRevisionResponse`——同一个端点根据 `action` 不同返回不同结构。FastAPI 会根据实际返回值自动选择对应的 response_model 做校验。

**确认时做防御性检查**：如果大纲为空（Agent 还没生成完就点了确认），返回 409 而不是悄悄保存空大纲。

**保存 `confirmed_outline` 副本**：确认后的快照独立保存，后续修改的是 `outline` 字段，`confirmed_outline` 不变。这为前端展示"已确认版本 vs 当前版本"的 diff 留下了空间。

### 4.4.4 提交报告生成

```python
@router.post("/research-projects/{project_id}/report-tasks", status_code=201)
async def create_report_task(project_id: str, request: ReportTaskCreate):
    project = await _get_project(project_id)

    # 状态门禁：只有已确认大纲的项目可以生成报告
    if project["status"] != ProjectStatus.OUTLINE_CONFIRMED:
        raise HTTPException(status_code=409,
            detail="请先确认研究大纲，再提交报告生成任务")

    task = await _create_task(
        project_id=project_id,
        task_type=TaskType.GENERATE_REPORT,
        message=request.user_instruction or "报告生成任务已创建",
    )

    await research_project_repository.update_project_status(
        project_id=project_id, status=ProjectStatus.RESEARCH_RUNNING)

    start_generate_report_task(
        project_id=project_id, task_id=task.task_id,
        user_instruction=request.user_instruction)

    return ReportTaskCreateResponse(...)
```

**状态门禁模式**：`if project["status"] != OUTLINE_CONFIRMED → 409`。这是 API 层面的约束，防止用户跳过"确认大纲"这个人工检查点直接让 AI 生成报告。

### 4.4.5 提交独立渲染

```python
@router.post("/research-projects/{project_id}/report-render-tasks", status_code=201)
async def create_report_render_task(project_id: str, request: ReportTaskCreate):
    project = await _get_project(project_id)

    # 不同门禁：必须有已保存的研究结果
    if not project.get("research_result"):
        raise HTTPException(status_code=409,
            detail="当前研究项目尚未生成研究结果，无法直接渲染报告")

    task = await _create_task(
        project_id=project_id, task_type=TaskType.RENDER_REPORT, ...)

    start_render_report_task(
        project_id=project_id, task_id=task.task_id,
        user_instruction=request.user_instruction)

    return ReportTaskCreateResponse(...)
```

与 `GENERATE_REPORT` 的区别：
- `GENERATE_REPORT`：完整流程，Agent 先研究后渲染
- `RENDER_REPORT`：跳过研究，只基于已落库的 `research_result` 重新渲染 HTML

使用场景：用户对报告样式不满意（如想换版式），但不需要重新检索资料。不需要再花时间/Token 跑一遍研究。

### 4.4.6 查询任务状态和获取报告

这两个是纯只读端点，逻辑简单：查 MongoDB → 转换 → 返回。

---

## 4.5 异步任务模式的 API 设计哲学

```
请求 → 创建记录 → 启动后台 → 立即返回 task_id
                                      ↓
前端轮询 GET /tasks/{task_id}  ────────┘
                                      ↓
                            status: succeeded → 获取结果
                            status: failed    → 显示错误
```

这种模式对应前端的典型交互：
1. 用户点击"生成报告"
2. 前端显示 loading spinner，开始轮询
3. 后台完成后，前端拿到最终结果

**为什么不用 WebSocket**：对于研究报告这种几分钟到十几分钟的任务，轮询足够简单。WebSocket 需要维护长连接，在小规模场景下收益不大。

---

## 4.6 路由层的错误处理策略

| 场景 | HTTP 状态码 | 示例 |
|------|------------|------|
| 项目不存在 | 404 | "研究项目不存在" |
| 任务不存在 | 404 | "后台任务不存在" |
| 状态不满足条件 | 409 | "请先确认研究大纲" |
| 数据尚未生成 | 409 | "尚未生成可确认的大纲" |
| 参数格式错误 | 422 | pydantic 自动校验 |
| 未知错误 | 500 | FastAPI 默认处理 |

路由层不 try-catch 后台任务的异常——后台任务失败通过 `mark_task_failed` 写入任务状态，不影响路由层返回。前端轮询时自然会看到 `failed` 状态。
