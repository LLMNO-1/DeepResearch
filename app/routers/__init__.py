"""API 路由层。

按研究项目生命周期顺序排列：
  创建项目 → 轮询任务 → 查看大纲 → 确认/修改大纲 → 生成报告 → 渲染报告 → 查看报告 → 导出报告 → 历史列表

内部辅助函数放在最前面，路由端点按用户操作流程从上到下排列。
"""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from loguru import logger

from app.background.research_tasks import (
    start_generate_report_task,
    start_generate_research_brief_task,
    start_render_report_task,
    start_revise_outline_task,
)
from app.repository import report_repository, research_project_repository, research_task_repository
from app.schemas import (
    LatestReportResponse,
    NextStep,
    OutlineAction,
    OutlineConfirmResponse,
    OutlineResponse,
    OutlineRevisionResponse,
    OutlineUpdateRequest,
    ProjectStatus,
    ReportTaskCreate,
    ReportTaskCreateResponse,
    ResearchProjectCreate,
    ResearchProjectCreateResponse,
    TaskStatus,
    TaskStatusResponse,
    TaskType,
    utc_now,
)
from app.tools.report_writer import build_report_document, render_report_html, render_report_markdown

router = APIRouter(tags=["研究项目"])


# -- 内部辅助 ------------------------------------------------------------

async def _create_task(project_id: str, task_type: TaskType, message: str) -> TaskStatusResponse:
    """创建一条 QUEUED 状态的任务记录，供前端轮询。

    被 4 个路由端点复用：create_research_project / update_outline(revise) / create_report_task /
    create_report_render_task。
    """

    now = utc_now()
    return await research_task_repository.create_task(
        task_id=str(uuid4()),
        project_id=project_id,
        task_type=task_type,
        status=TaskStatus.QUEUED,
        message=message,
        created_at=now,
        updated_at=now,
    )


async def _get_project(project_id: str) -> dict[str, object]:
    """根据 project_id 读取项目，不存在时直接抛 404。

    被 6 个路由端点复用，避免每个端点重复写存在性检查。
    """

    project = await research_project_repository.get_project(project_id=project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究项目不存在")
    return project


# -- 1. 创建项目 ---------------------------------------------------------

@router.post(
    "/research-projects",
    response_model=ResearchProjectCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_research_project(
    request: ResearchProjectCreate,
) -> ResearchProjectCreateResponse:
    """用户提交研究主题，系统创建项目并启动后台大纲生成任务。

    三步：写任务记录 → 写项目记录 → 提交后台 Agent 任务（不等结果）
    返回 project_id 和 task_id，前端拿到后开始轮询 GET /tasks/{task_id}。
    """

    project_id = str(uuid4())
    created_at = utc_now()
    task = await _create_task(
        project_id=project_id,
        task_type=TaskType.GENERATE_RESEARCH_BRIEF,
        message="研究任务书和大纲生成任务已创建",
    )
    await research_project_repository.create_project(
        project_id=project_id,
        request=request,
        topic=request.topic,
        status=ProjectStatus.BRIEF_GENERATING,
        created_at=created_at,
    )
    start_generate_research_brief_task(project_id=project_id, task_id=task.task_id)
    logger.info("创建研究项目成功，project_id={}，initial_task_id={}", project_id, task.task_id)
    return ResearchProjectCreateResponse(
        project_id=project_id,
        initial_task_id=task.task_id,
        initial_task_type=TaskType.GENERATE_RESEARCH_BRIEF,
        topic=request.topic,
        status=ProjectStatus.BRIEF_GENERATING,
        next_step=NextStep.WAIT_FOR_OUTLINE,
        created_at=created_at,
    )


# -- 2. 轮询任务状态 ------------------------------------------------------

@router.get("/tasks/{task_id}", response_model=TaskStatusResponse, tags=["后台任务"])
async def get_task(task_id: str) -> TaskStatusResponse:
    """前端每 2 秒轮询一次，获取后台任务的实时状态。

    被前端 startPolling() 调用，状态变化：queued → running → succeeded / failed。
    """

    task = await research_task_repository.get_task(task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="后台任务不存在")
    return task


# -- 3. 查看大纲 ---------------------------------------------------------

@router.get("/research-projects/{project_id}/outline", response_model=OutlineResponse)
async def get_outline(project_id: str) -> OutlineResponse:
    """前端轮询到任务成功后调用，获取大纲草案展示给用户。

    只读操作，不触发任何 Agent 或后台任务。
    """

    project = await _get_project(project_id)
    outline = await research_project_repository.get_outline(project_id=project_id)
    return OutlineResponse(
        project_id=project_id,
        status=project["status"],  # type: ignore[arg-type]
        outline=outline if isinstance(outline, list) else [],
    )


# -- 4. 确认 / 修改大纲 ---------------------------------------------------

@router.put(
    "/research-projects/{project_id}/outline",
    response_model=OutlineConfirmResponse | OutlineRevisionResponse,
)
async def update_outline(
    project_id: str,
    request: OutlineUpdateRequest,
) -> OutlineConfirmResponse | OutlineRevisionResponse:
    """两个分支：确认大纲直接推进状态；修改大纲则启动 revise 后台任务。

    action=confirm → 保存 confirmed_outline + 状态改为 OUTLINE_CONFIRMED
    action=revise  → 创建修订任务 + 启动 Agent 重新生成大纲
    """

    await _get_project(project_id)
    if request.action == OutlineAction.CONFIRM:
        outline = await research_project_repository.get_outline(project_id=project_id)
        if not outline:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前研究项目尚未生成可确认的大纲",
            )
        await research_project_repository.save_confirmed_outline(
            project_id=project_id,
            outline=outline,
        )
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_CONFIRMED,
        )
        logger.info("研究大纲已确认，project_id={}", project_id)
        return OutlineConfirmResponse(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_CONFIRMED,
            next_step=NextStep.GENERATE_REPORT,
        )

    task = await _create_task(
        project_id=project_id,
        task_type=TaskType.REVISE_OUTLINE,
        message="大纲修改任务已创建",
    )
    await research_project_repository.update_project_status(
        project_id=project_id,
        status=ProjectStatus.OUTLINE_REVISING,
    )
    start_revise_outline_task(
        project_id=project_id,
        task_id=task.task_id,
        revision_instruction=request.revision_instruction or "",
    )
    logger.info("研究大纲修改任务已创建，project_id={}，task_id={}", project_id, task.task_id)
    return OutlineRevisionResponse(
        project_id=project_id,
        revision_task_id=task.task_id,
        status=ProjectStatus.OUTLINE_REVISING,
        next_step=NextStep.WAIT_FOR_OUTLINE,
    )


# -- 5. 提交报告生成（研究 + 渲染）-----------------------------------------

@router.post(
    "/research-projects/{project_id}/report-tasks",
    response_model=ReportTaskCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_task(
    project_id: str,
    request: ReportTaskCreate,
) -> ReportTaskCreateResponse:
    """用户确认大纲后提交报告生成，后台先研究再渲染。

    前置条件：项目状态必须为 OUTLINE_CONFIRMED。
    后台执行：_run_generate_report_task → Agent 研究 → Agent 渲染 → 保存报告版本。
    """

    project = await _get_project(project_id)
    if project["status"] != ProjectStatus.OUTLINE_CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="请先确认研究大纲，再提交报告生成任务",
        )

    task = await _create_task(
        project_id=project_id,
        task_type=TaskType.GENERATE_REPORT,
        message=request.user_instruction or "报告生成任务已创建",
    )
    await research_project_repository.update_project_status(
        project_id=project_id,
        status=ProjectStatus.RESEARCH_RUNNING,
    )
    start_generate_report_task(
        project_id=project_id,
        task_id=task.task_id,
        user_instruction=request.user_instruction,
    )
    logger.info("报告生成任务已创建，project_id={}，task_id={}", project_id, task.task_id)
    return ReportTaskCreateResponse(
        task_id=task.task_id,
        project_id=project_id,
        task_type=TaskType.GENERATE_REPORT,
        status=TaskStatus.QUEUED,
    )


# -- 6. 独立报告渲染（不重新研究）------------------------------------------

@router.post(
    "/research-projects/{project_id}/report-render-tasks",
    response_model=ReportTaskCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_render_task(
    project_id: str,
    request: ReportTaskCreate,
) -> ReportTaskCreateResponse:
    """基于已落库 research_result 重新渲染 HTML，不调用 Agent 重新研究。

    前置条件：项目已有 research_result（即之前跑过研究阶段）。
    适用场景：调整了渲染参数后重新出报告。
    """

    project = await _get_project(project_id)
    if not project.get("research_result"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前研究项目尚未生成研究结果，无法直接渲染报告",
        )

    task = await _create_task(
        project_id=project_id,
        task_type=TaskType.RENDER_REPORT,
        message=request.user_instruction or "报告渲染任务已创建",
    )
    start_render_report_task(
        project_id=project_id,
        task_id=task.task_id,
        user_instruction=request.user_instruction,
    )
    logger.info("报告渲染任务已创建，project_id={}，task_id={}", project_id, task.task_id)
    return ReportTaskCreateResponse(
        task_id=task.task_id,
        project_id=project_id,
        task_type=TaskType.RENDER_REPORT,
        status=TaskStatus.QUEUED,
    )


# -- 7. 查看最新报告 ------------------------------------------------------

@router.get(
    "/research-projects/{project_id}/reports/latest",
    response_model=LatestReportResponse,
    tags=["研究报告"],
)
async def get_latest_report(project_id: str) -> LatestReportResponse:
    """前端轮询到任务成功后调用，获取最新版本的 HTML 报告。

    返回 html 字段包含内联 CSS 的完整 HTML，前端直接 innerHTML 渲染。
    """

    await _get_project(project_id)
    report = await report_repository.get_latest_report(project_id=project_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="研究报告不存在")
    return report


# -- 8. 导出报告 ---------------------------------------------------------

@router.get(
    "/research-projects/{project_id}/reports/export",
    tags=["研究报告"],
)
async def export_report(project_id: str, format: str = "md") -> Response:
    """将报告导出为 Markdown 或 HTML 文件下载。

    基于 research_result 实时渲染，不读 report_versions 集合。
    format=md  → Content-Type: text/markdown
    format=html → Content-Type: text/html
    """

    if format not in {"md", "html"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的导出格式，仅支持 md 和 html",
        )

    project = await _get_project(project_id)
    research_result = project.get("research_result")
    if not isinstance(research_result, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="当前研究项目尚未生成研究结果，无法导出",
        )

    document_ir = await build_report_document(research_result=research_result)

    if format == "md":
        rendered = await render_report_markdown(document_ir=document_ir)
        content = rendered["markdown"]
        media_type = "text/markdown; charset=utf-8"
        ext = "md"
    else:
        rendered = await render_report_html(document_ir=document_ir)
        content = rendered["html"]
        media_type = "text/html; charset=utf-8"
        ext = "html"

    title = rendered["title"]
    safe_title = "".join(c if c.isalnum() or c in "._- " else "_" for c in title).strip()
    filename = f"{safe_title}.{ext}"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


# -- 9. 历史报告列表 ------------------------------------------------------

@router.get("/research-projects", tags=["研究项目"])
async def list_research_projects(status: str | None = None) -> list[dict[str, object]]:
    """查询项目列表，前端"历史报告" Tab 使用。

    不传 status 返回全部；传 status=REPORT_READY 只返回已完成报告。
    返回字段精简（project_id / topic / status / created_at），不含正文和大纲。
    """

    return await research_project_repository.list_projects(status=status)


"""
  routers/__init__.py
  │
  ├── 内部辅助 (2 个，复用于 6-7 个端点)
  │   ├── _create_task   — 4 个端点复用
  │   └── _get_project   — 6 个端点复用
  │
  ├── 端点（按用户操作流程）
  │   ├── 1. POST /research-projects          — 创建项目 + 启动大纲生成
  │   ├── 2. GET  /tasks/{task_id}            — 前端轮询任务状态
  │   ├── 3. GET  /research-projects/{id}/outline — 查看大纲
  │   ├── 4. PUT  /research-projects/{id}/outline — 确认/修改大纲
  │   ├── 5. POST /research-projects/{id}/report-tasks — 生成报告(研究+渲染)
  │   ├── 6. POST /research-projects/{id}/report-render-tasks — 仅渲染
  │   ├── 7. GET  /research-projects/{id}/reports/latest — 查看最新报告
  │   ├── 8. GET  /research-projects/{id}/reports/export — 导出 MD/HTML
  │   └── 9. GET  /research-projects          — 历史报告列表

  改动点：
  - 按用户操作流程重排端点顺序，从上到下即完整用户路径
  - 每个端点注释说明调用方（前端哪个函数触发）、时机、前置条件
  - 删除了 create_research_project 里一个 14 行的示例 JSON 注释块（写在 return
  语句后面，永远不可达）
  - 两个内部辅助函数放在最前面，标注了各被哪些端点复用
"""