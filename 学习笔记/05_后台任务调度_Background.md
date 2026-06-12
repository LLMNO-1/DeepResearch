# 模块 5：后台任务调度 — Background

## 涉及文件

- `app/background/research_tasks.py` —— 4 种异步任务的具体执行流程

---

## 5.1 总体架构

background 层是"胶水层"——连接路由层和 Agent 层。每类后台任务遵循同样的模式：

```
start_xxx_task()                ← 由路由层调用，提交任务到事件循环
    └── _schedule_task()        ← asyncio.create_task() 统一入口
         └── _run_xxx_task()    ← 实际执行逻辑（async 协程）
              ├── 标记任务为 running
              ├── 更新项目状态
              ├── 调用 Agent
              ├── 保存结果到 repository
              ├── 标记任务为 succeeded
              └── 失败时 → _mark_task_failed()
```

---

## 5.2 任务调度入口

```python
def _schedule_task(coroutine, task_name, project_id, task_id):
    asyncio.create_task(coroutine, name=f"{task_name}:{task_id}")
    logger.info("后台任务已提交，task_name={}，project_id={}，task_id={}",
                task_name, project_id, task_id)
```

这是所有后台任务的统一入口。`asyncio.create_task` 把协程提交到当前事件循环，**立即返回**——不等待协程执行完毕。

**为什么不用 Celery/RQ**：
- 企业内部小规模使用，不需要分布式任务队列
- 单进程部署，不需要跨进程通信
- `asyncio.create_task` 零依赖，调试时可以直接看调用栈
- 代价是进程崩溃时未完成的任务会丢失（对研究报告场景可接受）

---

## 5.3 四种后台任务详解

### 5.3.1 研究任务书和大纲生成

```python
async def _run_generate_research_brief_task(project_id, task_id):
    try:
        await research_task_repository.mark_task_running(task_id, ...)
        await research_project_repository.update_project_status(
            project_id, ProjectStatus.BRIEF_GENERATING)

        project = await research_project_repository.get_project(project_id)
        research_agent = get_research_agent()
        result = await research_agent.generate_research_brief(project=project)

        await research_project_repository.save_research_brief_and_outline(
            project_id, result.research_brief, result.outline)
        await research_project_repository.update_project_status(
            project_id, ProjectStatus.OUTLINE_READY)
        await research_task_repository.mark_task_succeeded(task_id, ...)
    except Exception as exc:
        await _mark_task_failed(project_id, task_id,
            "研究任务书和大纲生成失败", exc)
```

流程：
1. 状态：任务 running + 项目 BRIEF_GENERATING
2. 获取项目数据，传给 Agent
3. Agent 调用 LLM 生成研究任务书和大纲
4. 结果保存到 MongoDB
5. 状态：项目 OUTLINE_READY + 任务 succeeded

**只有一条 Agent 调用**，没有循环、没有子任务协调。

### 5.3.2 大纲修改

```python
async def _run_revise_outline_task(project_id, task_id, revision_instruction):
    try:
        await research_task_repository.mark_task_running(task_id, ...)
        await research_project_repository.update_project_status(
            project_id, ProjectStatus.OUTLINE_REVISING)

        project = await research_project_repository.get_project(project_id)
        outline = await research_project_repository.get_outline(project_id)
        research_agent = get_research_agent()
        revised_outline = await research_agent.revise_outline(
            project=project, outline=outline,
            revision_instruction=revision_instruction)

        await research_project_repository.save_outline(project_id, revised_outline)
        await research_project_repository.update_project_status(
            project_id, ProjectStatus.OUTLINE_READY)
        await research_task_repository.mark_task_succeeded(task_id, ...)
    except Exception as exc:
        await _mark_task_failed(...)
```

与大纲生成类似，但输入多了当前大纲和用户修改要求。修改完成后状态回到 `OUTLINE_READY`，用户可以再次确认或继续修改。

### 5.3.3 报告生成（二段式——最复杂的任务）

```python
async def _run_generate_report_task(project_id, task_id, user_instruction):
    try:
        # 第一阶段：标记运行中
        await research_task_repository.mark_task_running(task_id, ...)
        await research_project_repository.update_project_status(
            project_id, ProjectStatus.RESEARCH_RUNNING)

        project = await research_project_repository.get_project(project_id)
        outline = await research_project_repository.get_confirmed_outline(project_id)
        research_agent = get_research_agent()

        # 第二阶段：执行研究（Agent 逐章节检索+撰写+落库）
        research_result = await research_agent.generate_research_result(
            project=project, outline=outline, user_instruction=user_instruction)
        await research_project_repository.save_research_result(
            project_id, research_result)

        # 第三阶段：重新读取项目（此时包含刚保存的 research_result）
        project_with_result = await research_project_repository.get_project(project_id)

        # 第四阶段：确定性 HTML 渲染
        result = await research_agent.generate_report(
            project=project_with_result, outline=outline,
            user_instruction=user_instruction)
        await report_repository.save_report_version(
            project_id, result.title, result.html, result.sources)

        await research_project_repository.update_project_status(
            project_id, ProjectStatus.REPORT_READY)
        await research_task_repository.mark_task_succeeded(task_id, ...)
    except Exception as exc:
        await _mark_task_failed(...)
```

**二段式设计**是关键：

```
研究阶段（generate_research_result）      渲染阶段（generate_report）
  Agent 检索 + 写章节 + 落库        →      确定性代码读 research_result
  耗时可能几分钟                           生成 HTML，耗时毫秒级
  涉及 LLM 多次调用                        不调用 LLM
  产出的 research_result 落库后              可以反复重新渲染
  可独立于渲染被重新执行                     不修改研究结果
```

**为什么要重新 `get_project`**：`generate_research_result` 过程中，Agent 通过 `save_research_section` 工具逐章节写入数据库。但 `research_agent.generate_result()` 的返回值是组装好的 `ResearchResult` 对象。保存后重新读取项目，确保渲染阶段拿到的是最新、最完整的数据库状态。

### 5.3.4 独立报告渲染

```python
async def _run_render_report_task(project_id, task_id, user_instruction):
    try:
        project = await research_project_repository.get_project(project_id)
        if not project.get("research_result"):
            raise ValueError("项目缺少已落库的 research_result")

        outline = await research_project_repository.get_confirmed_outline(project_id)
        research_agent = get_research_agent()
        result = await research_agent.generate_report(
            project=project, outline=outline, user_instruction=user_instruction)
        await report_repository.save_report_version(...)

        await research_project_repository.update_project_status(
            project_id, ProjectStatus.REPORT_READY)
        await research_task_repository.mark_task_succeeded(task_id, ...)
    except Exception as exc:
        await _mark_task_failed(...)
```

只做渲染，不重新研究。与 `_run_generate_report_task` 的区别：跳过了 `generate_research_result`，直接从数据库读已有的研究结果来渲染。

**应用场景**：用户对上一版报告的排版不满意（比如想换主题配色），不需要花 Token 重新检索。

---

## 5.4 统一的错误处理

```python
async def _mark_task_failed(project_id, task_id, message, exc):
    error_message = _build_task_error_message(message=message, exc=exc)
    logger.exception(
        "后台任务执行失败，project_id={}，task_id={}，error={}，"
        "exception_detail={}，exception_attrs={}",
        project_id, task_id, error_message, str(exc),
        _extract_exception_attrs(exc),
    )
    await research_task_repository.mark_task_failed(
        task_id=task_id, message=error_message)
```

做的三件事：
1. **构建安全错误摘要**：只取异常类型和截断消息，不输出 API Key
2. **记录详细日志**：`logger.exception` 自动附带完整 traceback，额外提取常见 LLM/HTTP 异常属性
3. **写入任务状态**：让前端能展示失败原因

### 异常属性提取

```python
def _extract_exception_attrs(exc):
    attrs = {}
    for name in ("status_code", "code", "type", "param", "request_id",
                 "body", "response", "message"):
        if hasattr(exc, name):
            attrs[name] = _safe_repr(getattr(exc, name))
    # 也记录 args 和 __dict__
    return attrs
```

这是为了兼容 OpenAI/DeepSeek SDK 的异常类型。`BadRequestError` 可能带有 `status_code=400`、`type="invalid_request_error"`、`param="messages"` 等属性，提取出来帮助排查问题。

### 安全截断

```python
def _safe_repr(value, max_length=4000):
    text = repr(value)
    if len(text) > max_length:
        return text[:max_length] + "...<truncated>"
    return text
```

防止大对象（如完整的 HTTP Response body）撑爆日志。

---

## 5.5 四个 start 函数的公共入口

```python
def start_generate_research_brief_task(project_id, task_id):
    _schedule_task(
        _run_generate_research_brief_task(project_id, task_id),
        task_name="generate_research_brief",
        project_id=project_id, task_id=task_id)

def start_revise_outline_task(project_id, task_id, revision_instruction):
    _schedule_task(
        _run_revise_outline_task(project_id, task_id, revision_instruction),
        ...)

def start_generate_report_task(project_id, task_id, user_instruction):
    _schedule_task(
        _run_generate_report_task(project_id, task_id, user_instruction),
        ...)

def start_render_report_task(project_id, task_id, user_instruction):
    _schedule_task(
        _run_render_report_task(project_id, task_id, user_instruction),
        ...)
```

每个 `start_xxx` 函数接收路由层传入的参数，创建协程、提交到事件循环。路由层不需要知道事件循环的存在。

---

## 5.6 与路由层的协作模式

```
路由层（同步思维）
  │  def endpoint():
  │      task = await create_task(...)
  │      start_xxx_task(...)        ← fire-and-forget
  │      return Response(task_id=...)  ← 立即返回
  │
后台层（异步执行）
  │  async def _run_xxx_task():
  │      await mark_task_running(...)
  │      result = await agent.xxx(...)  ← 可能耗时几分钟
  │      await save_result(...)
  │      await mark_task_succeeded(...)
```

路由层和后台层通过**任务状态表**解耦。路由层不知道后台任务的执行细节，后台层不关心 HTTP 响应格式。
