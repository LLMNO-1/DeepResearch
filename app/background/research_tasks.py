import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger

from app.agents.research_agent import get_research_agent
from app.repository import report_repository, research_project_repository, research_task_repository
from app.schemas import ProjectStatus

# ══════════════════════════════════════════════════════════════════════════════
# 流程一：生成研究任务书和大纲
# 路由层 POST /api/v1/research-projects → start → _schedule_task → _run
# ══════════════════════════════════════════════════════════════════════════════


def start_generate_research_brief_task(project_id: str, task_id: str) -> None:
    """路由层入口：启动研究任务书和大纲生成后台任务。

    输入为研究项目编号和后台任务编号；该函数只负责把任务提交到当前 API 进程的
    asyncio 事件循环，不直接执行 Agent，也不返回任务结果。
    """

    _schedule_task(
        _run_generate_research_brief_task(project_id=project_id, task_id=task_id),
        task_name="generate_research_brief",
        project_id=project_id,
        task_id=task_id,
    )


async def _run_generate_research_brief_task(project_id: str, task_id: str) -> None:
    """实际执行：生成研究任务书和大纲并保存到 MongoDB。

    输入为项目编号和任务编号；执行过程会读取研究项目、调用研究管理智能体生成
    任务书与大纲，并把结果保存到 repository。该函数不处理 HTTP 响应。
  1. mark_task_running(task_id)          → 任务状态: RUNNING
  2. update_project_status(BRIEF_GENERATING) → 项目状态: BRIEF_GENERATING
  3. get_project(project_id)             → 从 MongoDB 读出你的原始输入
  4. agent.generate_research_brief(project) → ★ 调 Agent
  5. save_research_brief_and_outline()   → 把大纲写回 MongoDB
  6. update_project_status(OUTLINE_READY) → 项目状态: OUTLINE_READY
  7. mark_task_succeeded(task_id)        → 任务状态: SUCCESS
    """

    try:
        await research_task_repository.mark_task_running(
            task_id=task_id,
            message="正在生成研究任务书和大纲",
        )
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.BRIEF_GENERATING,
        )
        logger.info("开始生成研究任务书和大纲，project_id={}，task_id={}", project_id, task_id)

        project = await research_project_repository.get_project(project_id=project_id)
        research_agent = get_research_agent()
        result = await research_agent.generate_research_brief(project=project)

        await research_project_repository.save_research_brief_and_outline(
            project_id=project_id,
            research_brief=result.research_brief,
            outline=result.outline,
        )
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_READY,
        )
        await research_task_repository.mark_task_succeeded(
            task_id=task_id,
            message="研究任务书和大纲已生成，等待用户确认",
        )
        logger.info("研究任务书和大纲生成完成，project_id={}，task_id={}", project_id, task_id)
    except Exception as exc:
        await _mark_task_failed(
            project_id=project_id,
            task_id=task_id,
            message="研究任务书和大纲生成失败",
            exc=exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 流程二：修改研究大纲
# 路由层 PUT /outline (REVISE) → start → _schedule_task → _run
# ══════════════════════════════════════════════════════════════════════════════


def start_revise_outline_task(
    project_id: str,
    task_id: str,
    revision_instruction: str,
) -> None:
    """路由层入口：启动研究大纲修改后台任务。

    输入为研究项目编号、后台任务编号和用户的自然语言修改要求；该函数只负责启动
    后台执行协程，具体的大纲修改由研究管理智能体完成。
    """

    _schedule_task(
        _run_revise_outline_task(
            project_id=project_id,
            task_id=task_id,
            revision_instruction=revision_instruction,
        ),
        task_name="revise_outline",
        project_id=project_id,
        task_id=task_id,
    )


async def _run_revise_outline_task(
    project_id: str,
    task_id: str,
    revision_instruction: str,
) -> None:
    """实际执行：根据用户修改要求修订大纲并保存。

    输入为项目编号、任务编号和用户修改要求；执行过程会读取当前大纲，调用研究管理
    智能体产出修订版大纲，并保存回项目记录。
    """

    try:
        await research_task_repository.mark_task_running(
            task_id=task_id,
            message="正在根据用户要求修改研究大纲",
        )
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_REVISING,
        )
        logger.info("开始修改研究大纲，project_id={}，task_id={}", project_id, task_id)

        project = await research_project_repository.get_project(project_id=project_id)
        outline = await research_project_repository.get_outline(project_id=project_id)
        research_agent = get_research_agent()
        revised_outline = await research_agent.revise_outline(
            project=project,
            outline=outline,
            revision_instruction=revision_instruction,
        )

        await research_project_repository.save_outline(
            project_id=project_id,
            outline=revised_outline,
        )
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_READY,
        )
        await research_task_repository.mark_task_succeeded(
            task_id=task_id,
            message="研究大纲已修改，等待用户确认",
        )
        logger.info("研究大纲修改完成，project_id={}，task_id={}", project_id, task_id)
    except Exception as exc:
        await _mark_task_failed(
            project_id=project_id,
            task_id=task_id,
            message="研究大纲修改失败",
            exc=exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 流程三：生成研究报告（研究 + 渲染）
# 路由层 POST /report-tasks → start → _schedule_task → _run
# ══════════════════════════════════════════════════════════════════════════════


def start_generate_report_task(
    project_id: str,
    task_id: str,
    user_instruction: str | None,
) -> None:
    """路由层入口：启动研究报告生成后台任务。

    输入为研究项目编号、后台任务编号和可选的研究/报告要求；该函数只负责启动后台
    执行协程，研究结果和报告版本保存由内部执行流程完成。
    """

    _schedule_task(
        _run_generate_report_task(
            project_id=project_id,
            task_id=task_id,
            user_instruction=user_instruction,
        ),
        task_name="generate_report",
        project_id=project_id,
        task_id=task_id,
    )


async def _run_generate_report_task(
    project_id: str,
    task_id: str,
    user_instruction: str | None,
) -> None:
    """实际执行：调用 Agent 研究并写入章节，再渲染为 HTML 报告。

    输入为项目编号、任务编号和可选的报告生成要求；执行过程会读取已确认大纲，
    先调用研究管理智能体完成研究结果落库，再调用确定性报告渲染流程生成 HTML。
    """

    try:
        # 将后台任务标记为"执行中"
        await research_task_repository.mark_task_running(
            task_id=task_id,
            message="正在执行研究并生成报告",
        )
        # 将项目状态更新为"研究中"
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.RESEARCH_RUNNING,
        )
        logger.info("开始执行研究和报告渲染，project_id={}，task_id={}", project_id, task_id)

        # 从 MongoDB 读取项目文档
        project = await research_project_repository.get_project(project_id=project_id)
        # 读取用户已确认的研究大纲
        outline = await research_project_repository.get_confirmed_outline(project_id=project_id)
        # 获取 DeepAgents 研究智能体单例
        research_agent = get_research_agent()

        # Agent 研究：ManagerAgent 协调 SearchAgent 搜索+检索，撰写章节正文并逐章落库
        research_result = await research_agent.generate_research_result(
            project=project,
            outline=outline,
            user_instruction=user_instruction,
        )
        # 将研究结果（所有章节）持久化到 MongoDB
        await research_project_repository.save_research_result(
            project_id=project_id,
            research_result=research_result,
        )
        logger.info(
            "研究结果已保存，project_id={}，task_id={}，sections={}",
            project_id,
            task_id,
            len(research_result.sections),
        )

        # 重新读取项目，获取刚写入的 research_result
        project_with_research_result = await research_project_repository.get_project(
            project_id=project_id
        )
        # 确定性渲染：纯 Python 将研究结果转为 HTML，不调 LLM
        result = await research_agent.generate_report(
            project=project_with_research_result,
            outline=outline,
            user_instruction=user_instruction,
        )
        # 保存报告版本（HTML + 来源列表）
        await report_repository.save_report_version(
            project_id=project_id,
            title=result.title,
            html=result.html,
            sources=result.sources,
        )
        # 项目状态更新为"报告就绪"
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.REPORT_READY,
        )
        # 后台任务标记为"成功"
        await research_task_repository.mark_task_succeeded(
            task_id=task_id,
            message="研究报告已生成",
        )
        logger.info("研究和报告渲染完成，project_id={}，task_id={}", project_id, task_id)
    except Exception as exc:
        # 异常路径：项目置为 FAILED，任务标记失败并记录错误日志
        await _mark_task_failed(
            project_id=project_id,
            task_id=task_id,
            message="研究报告生成失败",
            exc=exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 流程四：独立报告渲染（不重新研究，仅基于已有 research_result 渲染）
# 路由层 POST /report-render-tasks → start → _schedule_task → _run
# ══════════════════════════════════════════════════════════════════════════════


def start_render_report_task(
    project_id: str,
    task_id: str,
    user_instruction: str | None,
) -> None:
    """路由层入口：启动独立报告渲染后台任务。

    输入为研究项目编号、后台任务编号和可选展示要求；该任务只读取已落库的
    research_result 并生成 HTML 报告版本，不重新执行研究。
    """

    _schedule_task(
        _run_render_report_task(
            project_id=project_id,
            task_id=task_id,
            user_instruction=user_instruction,
        ),
        task_name="render_report",
        project_id=project_id,
        task_id=task_id,
    )


async def _run_render_report_task(
    project_id: str,
    task_id: str,
    user_instruction: str | None,
) -> None:
    """实际执行：读取已落库 research_result，调用渲染流程生成 HTML 报告。

    输入为项目编号、任务编号和可选展示要求；执行过程只读取已保存的 research_result，
    调用确定性报告渲染流程生成 HTML 并保存报告版本，不触发主研究智能体。
    """

    try:
        await research_task_repository.mark_task_running(
            task_id=task_id,
            message="正在基于已有研究结果渲染报告",
        )
        logger.info("开始独立渲染报告，project_id={}，task_id={}", project_id, task_id)

        project = await research_project_repository.get_project(project_id=project_id)
        if not isinstance(project, dict) or not project.get("research_result"):
            raise ValueError("项目缺少已落库的 research_result，无法直接渲染报告")

        outline = await research_project_repository.get_confirmed_outline(project_id=project_id)
        research_agent = get_research_agent()
        result = await research_agent.generate_report(
            project=project,
            outline=outline,
            user_instruction=user_instruction,
        )
        await report_repository.save_report_version(
            project_id=project_id,
            title=result.title,
            html=result.html,
            sources=result.sources,
        )
        await research_project_repository.update_project_status(
            project_id=project_id,
            status=ProjectStatus.REPORT_READY,
        )
        await research_task_repository.mark_task_succeeded(
            task_id=task_id,
            message="报告已基于已有研究结果生成",
        )
        logger.info("独立报告渲染完成，project_id={}，task_id={}", project_id, task_id)
    except Exception as exc:
        await _mark_task_failed(
            project_id=project_id,
            task_id=task_id,
            message="独立报告渲染失败",
            exc=exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# 公共基础设施：将协程提交到当前事件循环
# 被上方四个 start_* 函数调用
# ══════════════════════════════════════════════════════════════════════════════


def _schedule_task(
    coroutine: Coroutine[Any, Any, None],
    task_name: str,
    project_id: str,
    task_id: str,
) -> None:
    """把后台协程提交到当前事件循环。

    输入为待执行协程、任务名称、项目编号和任务编号；输出为空。该函数隔离
    asyncio.create_task，保证 routers 层不直接依赖具体的后台任务启动方式。
    """
    #它接收一个协程对象，把它注册到当前运行,的事件循环中，立即返回（不等待协程执行完成）。事件循环会在空闲时调度执行这个协程。
    asyncio.create_task(coroutine, name=f"{task_name}:{task_id}")
    logger.info(
        "后台任务已提交，task_name={}，project_id={}，task_id={}",
        task_name,
        project_id,
        task_id,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 错误处理：统一记录后台任务失败状态
# 被上方四个 _run_*_task 的 except 分支调用
# ══════════════════════════════════════════════════════════════════════════════


async def _mark_task_failed(
    project_id: str,
    task_id: str,
    message: str,
    exc: Exception,
) -> None:
    """统一记录后台任务失败状态。

    输入为项目编号、任务编号、业务失败说明和异常对象；输出为空。该函数只写入必要
    的错误摘要和日志，不输出 API Key、访问令牌或用户隐私原文。
    """

    error_message = _build_task_error_message(message=message, exc=exc)
    logger.exception(
        "后台任务执行失败，project_id={}，task_id={}，error={}，exception_detail={}，exception_attrs={}",
        project_id,
        task_id,
        error_message,
        str(exc),
        _extract_exception_attrs(exc),
    )
    await research_task_repository.mark_task_failed(
        task_id=task_id,
        message=error_message,
    )


# 由 _mark_task_failed 调用的工具函数


def _build_task_error_message(message: str, exc: Exception) -> str:
    """构建写入任务状态的短错误摘要。"""

    detail = str(exc).strip()
    if detail:
        return f"{message}: {type(exc).__name__}: {detail[:500]}"
    return f"{message}: {type(exc).__name__}"


def _extract_exception_attrs(exc: Exception) -> dict[str, Any]:
    """提取常见 LLM/HTTP 异常字段，便于定位 BadRequestError。"""

    attrs: dict[str, Any] = {}
    for name in (
        "status_code",
        "code",
        "type",
        "param",
        "request_id",
        "body",
        "response",
        "message",
    ):
        if not hasattr(exc, name):
            continue
        value = getattr(exc, name)
        attrs[name] = _safe_repr(value)
    if getattr(exc, "args", None):
        attrs["args"] = _safe_repr(exc.args)
    if getattr(exc, "__dict__", None):
        attrs["dict"] = _safe_repr(exc.__dict__)
    return attrs


def _safe_repr(value: Any, max_length: int = 4000) -> str:
    """返回适合日志记录的短文本表示。"""

    text = repr(value)
    if len(text) > max_length:
        return text[:max_length] + "...<truncated>"
    return text


"""
  排版逻辑： 按项目生命周期的四个流程从上到下排列，每个流程组内 start_*（路由层入口）→
  _run_*（实际执行），底部是各流程共享的 _schedule_task 和错误处理工具函数。

  process1: start_generate_research_brief_task → _run_generate_research_brief_task
  process2: start_revise_outline_task           → _run_revise_outline_task
  process3: start_generate_report_task          → _run_generate_report_task
  process4: start_render_report_task            → _run_render_report_task
                          ↓ 共享调用
                _schedule_task          （协程提交）
                _mark_task_failed       （错误记录）
                _build_task_error_message
                _extract_exception_attrs
                _safe_repr

"""