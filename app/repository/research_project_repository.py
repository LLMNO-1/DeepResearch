"""研究项目数据访问层。

按研究项目生命周期顺序排列，从上到下即完整执行链路：
  创建 → 读取 → 状态更新 → 大纲生成/修改/确认 → 研究执行(清空→逐章落库→聚合) → 列表查询

内部辅助函数统一放在文件末尾。
"""
from datetime import datetime
from typing import Any

from app.repository.mongodb import get_mongodb_database
from app.schemas import OutlineNode, ProjectStatus, ResearchProjectCreate, ReportSource, utc_now

COLLECTION_NAME = "research_projects"


# =============================================================================
# 1. 项目创建 —— POST /research-projects
# =============================================================================

async def create_project(
    project_id: str,
    request: ResearchProjectCreate,
    topic: str,
    status: ProjectStatus,
    created_at: datetime,
) -> dict[str, Any]:
    """创建研究项目记录，初始化所有字段的默认值。

    调用方：routers/__init__.py create_research_project()
    时机：用户提交研究主题后立即执行，在启动 Agent 之前
    """

    document: dict[str, Any] = {
        "_id": project_id,
        "project_id": project_id,
        "topic": topic,
        "request": request.model_dump(mode="python"),
        "status": status,                     # ProjectStatus.BRIEF_GENERATING
        "outline": [],
        "confirmed_outline": [],
        "research_brief": None,
        "research_result": None,
        "sections": [],
        "sources": [],
        "fact_cards": [],
        "insight_cards": [],
        "created_at": created_at,
        "updated_at": created_at,
    }
    await _get_collection().insert_one(document)
    return _clean_document(document) or {}


# =============================================================================
# 2. 项目读取 —— 贯穿全生命周期，所有阶段都需要
# =============================================================================

async def get_project(project_id: str) -> dict[str, Any] | None:
    """根据 project_id 读取完整项目文档。

    调用方：
    - routers/__init__.py _get_project()        → 路由层校验项目是否存在
    - background/research_tasks.py               → 四个后台任务都需要读项目数据
    - tools/research_workspace.py _validate_section() → 校验章节时确认项目存在
    项目不存在时返回 None，由调用方决定是否报 404。
    """

    document = await _get_collection().find_one({"project_id": project_id})
    return _clean_document(document)


# =============================================================================
# 3. 状态更新 —— 贯穿全生命周期，每个阶段切换时调用
# =============================================================================

async def update_project_status(project_id: str, status: ProjectStatus) -> None:
    """更新项目主流程状态和更新时间。

    调用方：
    - routers/__init__.py           → 大纲确认/修改/报告任务创建时
    - background/research_tasks.py  → 四个后台任务的开始和结束时

    状态流转路径：
    CREATED → BRIEF_GENERATING → OUTLINE_READY → OUTLINE_REVISING → OUTLINE_READY
                                                  → OUTLINE_CONFIRMED
    → RESEARCH_RUNNING → REPORT_READY → COMPLETED
    """

    await _get_collection().update_one(
        {"project_id": project_id},
        {"$set": {"status": status, "updated_at": utc_now()}},
    )


# =============================================================================
# 4. 大纲生成 —— Agent 生成任务书和大纲后落库
# =============================================================================

async def save_research_brief_and_outline(
    project_id: str,
    research_brief: Any,
    outline: list[OutlineNode] | list[dict[str, Any]],
) -> None:
    """保存 Agent 产出的研究任务书和大纲草案。

    调用方：background/research_tasks.py _run_generate_research_brief_task()
    时机：Manager Agent 完成 generate_research_brief 后
    写入字段：research_brief + outline，同时更新 updated_at
    """

    await _get_collection().update_one(
        {"project_id": project_id},
        {
            "$set": {
                "research_brief": _dump_value(research_brief),
                "outline": _dump_outline(outline),
                "updated_at": utc_now(),
            }
        },
    )


# =============================================================================
# 5. 大纲读取 —— 用户查看大纲 / 大纲修改前获取当前版本
# =============================================================================

async def get_outline(project_id: str) -> list[OutlineNode]:
    """读取当前大纲草案（可能未确认）。

    调用方：
    - routers/__init__.py get_outline()          → 前端展示大纲
    - routers/__init__.py update_outline()       → 确认前校验大纲存在
    - background/research_tasks.py                → 大纲修改前获取原大纲
    大纲不存在时返回空列表。
    """

    document = await _get_collection().find_one({"project_id": project_id}, {"outline": 1})
    if document is None:
        return []
    return [OutlineNode.model_validate(node) for node in document.get("outline", [])]


# =============================================================================
# 6. 大纲修改 —— 用户提交修改意见后 Agent 产出新版大纲
# =============================================================================

async def save_outline(
    project_id: str,
    outline: list[OutlineNode] | list[dict[str, Any]],
) -> None:
    """用修订版大纲覆盖当前大纲草案。

    调用方：background/research_tasks.py _run_revise_outline_task()
    时机：Manager Agent 完成 revise_outline 后
    只覆盖 outline 字段，不影响已确认的 confirmed_outline。
    """

    await _get_collection().update_one(
        {"project_id": project_id},
        {"$set": {"outline": _dump_outline(outline), "updated_at": utc_now()}},
    )


# =============================================================================
# 7. 大纲确认 —— 用户点"确认大纲"后存档
# =============================================================================

async def save_confirmed_outline(
    project_id: str,
    outline: list[OutlineNode] | list[dict[str, Any]],
) -> None:
    """将当前大纲保存为已确认版本。

    调用方：routers/__init__.py update_outline() action=confirm
    时机：用户确认大纲时
    写入 confirmed_outline 字段，后续研究阶段只读这个字段。
    """

    await _get_collection().update_one(
        {"project_id": project_id},
        {"$set": {"confirmed_outline": _dump_outline(outline), "updated_at": utc_now()}},
    )


# =============================================================================
# 8. 已确认大纲读取 —— 研究阶段只读确认版，不读草案
# =============================================================================

async def get_confirmed_outline(project_id: str) -> list[OutlineNode]:
    """读取已确认大纲，兜底回退到 outline 草案。

    调用方：background/research_tasks.py _run_generate_report_task() 和 _run_render_report_task()
    时机：研究执行和报告渲染阶段
    优先读 confirmed_outline，不存在时回退读 outline（兼容旧数据）。
    """

    document = await _get_collection().find_one(
        {"project_id": project_id},
        {"outline": 1, "confirmed_outline": 1},
    )
    if document is None:
        return []
    outline = document.get("confirmed_outline") or document.get("outline", [])
    return [OutlineNode.model_validate(node) for node in outline]


# =============================================================================
# 9. 研究执行前 —— 清空旧章节，避免新旧数据混杂
# =============================================================================

async def clear_research_sections(project_id: str) -> None:
    """清空上一次研究的所有中间产物。

    调用方：agents/research_agent.py generate_research_result()
    时机：每次执行研究前，在逐章节循环开始之前
    清空：sections / sources / fact_cards / insight_cards / research_result
    不清空：topic / request / outline / confirmed_outline / research_brief
    """

    await _get_collection().update_one(
        {"project_id": project_id},
        {
            "$set": {
                "sections": [],
                "sources": [],
                "fact_cards": [],
                "insight_cards": [],
                "research_result": None,
                "updated_at": utc_now(),
            }
        },
    )


# =============================================================================
# 10. 逐章节落库 —— Agent 每写完一章就调用一次
# =============================================================================

async def upsert_research_section(project_id: str, section: dict[str, Any]) -> None:
    """按 section_id 写入或覆盖单个章节（先删后插实现 upsert）。

    调用方：tools/research_workspace.py save_research_section()
    时机：Manager Agent 每写完一个章节后调用，通过工具函数间接调用
    策略：先 $pull 同 section_id 的旧章节 → 再 $push 新章节
    """

    section_id = section.get("section_id")
    await _get_collection().update_one(
        {"project_id": project_id},
        {"$pull": {"sections": {"section_id": section_id}}},
    )
    await _get_collection().update_one(
        {"project_id": project_id},
        {"$push": {"sections": section}, "$set": {"updated_at": utc_now()}},
    )


async def upsert_research_sources(project_id: str, sources: list[dict[str, Any]]) -> None:
    """逐章节同步来源到项目级 sources 数组（按 source_id 或 url 去重 upsert）。

    调用方：tools/research_workspace.py save_research_section()
    时机：每保存一个章节后同步该章节引用的来源
    """

    now = utc_now()
    for source in sources:
        source_key = str(
            source.get("source_id")
            or source.get("url")
            or source.get("title")
            or ""
        ).strip()
        if not source_key:
            continue
        if source.get("source_id"):
            await _get_collection().update_one(
                {"project_id": project_id},
                {"$pull": {"sources": {"source_id": source.get("source_id")}}},
            )
        if source.get("url"):
            await _get_collection().update_one(
                {"project_id": project_id},
                {"$pull": {"sources": {"url": source.get("url")}}},
            )
        await _get_collection().update_one(
            {"project_id": project_id},
            {"$push": {"sources": source}, "$set": {"updated_at": now}},
        )


# =============================================================================
# 11. 研究进度检查 —— Agent 每轮循环后查 MongoDB 确认哪些章节已落库
# =============================================================================

async def get_research_sections(project_id: str) -> list[dict[str, Any]]:
    """读取当前项目所有已落库章节，按 section_id 排序。

    调用方：agents/research_agent.py generate_research_result()
    时机：每轮 Agent 调用后，检查哪些 section_id 已保存、哪些缺失
    排序保证报告渲染时章节顺序稳定。
    """

    document = await _get_collection().find_one({"project_id": project_id}, {"sections": 1})
    if document is None:
        return []
    sections = [section for section in document.get("sections", []) if isinstance(section, dict)]
    return sorted(sections, key=lambda section: str(section.get("section_id") or ""))


async def get_research_sources(project_id: str) -> list[dict[str, Any]]:
    """读取当前项目所有已落库来源。

    调用方：agents/research_agent.py _collect_saved_sources()
    时机：研究结果聚合阶段，合并项目级和章节级来源
    """

    document = await _get_collection().find_one({"project_id": project_id}, {"sources": 1})
    if document is None:
        return []
    return [source for source in document.get("sources", []) if isinstance(source, dict)]


# =============================================================================
# 12. 研究结果聚合保存 —— 研究完成后一次性落库
# =============================================================================

async def save_research_result(project_id: str, research_result: Any) -> None:
    """保存完整研究结果，委托 save_research_results 执行。

    调用方：background/research_tasks.py _run_generate_report_task()
    时机：Agent 逐章节研究全部完成后
    效果：把 research_result 拆成 sources/fact_cards/insight_cards/sections 写入
    """

    dumped = _dump_value(research_result)
    if not isinstance(dumped, dict):
        dumped = {}
    await save_research_results(
        project_id=project_id,
        sources=dumped.get("sources", []),
        fact_cards=dumped.get("fact_cards", []),
        insight_cards=dumped.get("insight_cards", []),
        research_result=dumped,
    )


async def save_research_results(
    project_id: str,
    sources: list[ReportSource] | list[dict[str, Any]],
    fact_cards: list[Any],
    insight_cards: list[Any],
    research_result: Any | None = None,
) -> None:
    """底层保存：写入 sources / fact_cards / insight_cards / research_result。

    被 save_research_result 调用，也保留直接调用的灵活性。
    research_result 不为 None 时同步写入 sections 字段。
    """

    update_fields: dict[str, Any] = {
        "sources": _dump_sources(sources),
        "fact_cards": [_dump_value(card) for card in fact_cards],
        "insight_cards": [_dump_value(card) for card in insight_cards],
        "updated_at": utc_now(),
    }
    if research_result is not None:
        dumped_rr = _dump_value(research_result)
        update_fields["research_result"] = dumped_rr
        if isinstance(dumped_rr, dict):
            update_fields["sections"] = dumped_rr.get("sections", [])

    await _get_collection().update_one(
        {"project_id": project_id},
        {"$set": update_fields},
    )


# =============================================================================
# 13. 项目列表查询 —— 历史报告页 / 管理后台
# =============================================================================

async def list_projects(
    status: str | None = None,
    limit: int = 50,
    skip: int = 0,
) -> list[dict[str, Any]]:
    """按创建时间倒序查询项目列表，支持按状态过滤。

    调用方：routers/__init__.py list_research_projects()
    时机：前端"历史报告" Tab 加载时，传 status=REPORT_READY 只查已完成报告
    返回字段精简（project_id / topic / status / created_at），不含大纲和研究结果正文。
    """

    query: dict[str, Any] = {}
    if status:
        query["status"] = status

    cursor = (
        _get_collection()
        .find(query, {"project_id": 1, "topic": 1, "status": 1, "created_at": 1})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    results: list[dict[str, Any]] = []
    async for doc in cursor:
        cleaned = _clean_document(doc)
        if cleaned:
            results.append(cleaned)
    return results


# =============================================================================
# 内部辅助函数
# =============================================================================

def _get_collection():
    """获取 MongoDB research_projects 集合对象。"""
    return get_mongodb_database()[COLLECTION_NAME]


def _clean_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    """移除 MongoDB 内部 _id 字段，恢复 status 为枚举类型。"""
    if document is None:
        return None
    document.pop("_id", None)
    if "status" in document:
        document["status"] = ProjectStatus(str(document["status"]))
    return document


def _dump_value(value: Any) -> Any:
    """Pydantic 对象 → dict，普通值原样返回。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    return value


def _dump_outline(outline: list[OutlineNode] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """大纲节点列表 → 可写入 MongoDB 的 dict 列表。"""
    dumped: list[dict[str, Any]] = []
    for node in outline:
        if isinstance(node, OutlineNode):
            dumped.append(node.model_dump(mode="python"))
        else:
            dumped.append(node)
    return dumped


def _dump_sources(sources: list[ReportSource] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """来源列表 → 可写入 MongoDB 的 dict 列表。"""
    dumped: list[dict[str, Any]] = []
    for source in sources:
        if isinstance(source, ReportSource):
            dumped.append(source.model_dump(mode="python"))
        else:
            dumped.append(source)
    return dumped

"""
16 个公开方法，全部被调用，没有废方法：

  ┌──────┬─────────────────────────────────┬──────────────────────────────┬──────────┐
  │ 序号 │              方法               │            调用方            │ 调用次数 │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 1    │ create_project                  │ routers                      │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 2    │ get_project                     │ routers + background + tools │ 7 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 3    │ update_project_status           │ routers + background         │ 10 处    │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 4    │ save_research_brief_and_outline │ background                   │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 5    │ get_outline                     │ routers + background         │ 3 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 6    │ save_outline                    │ background                   │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 7    │ save_confirmed_outline          │ routers                      │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 8    │ get_confirmed_outline           │ background                   │ 2 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 9    │ clear_research_sections         │ agents                       │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 10   │ upsert_research_section         │ tools                        │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 11   │ upsert_research_sources         │ tools                        │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 12   │ get_research_sections           │ agents                       │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 13   │ get_research_sources            │ agents                       │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 14   │ save_research_result            │ background                   │ 1 处     │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 15   │ save_research_results           │ 被 14 内部调用               │ —        │
  ├──────┼─────────────────────────────────┼──────────────────────────────┼──────────┤
  │ 16   │ list_projects                   │ routers                      │ 1 处     │
  └──────┴─────────────────────────────────┴──────────────────────────────┴──────────┘

  5 个内部辅助：_get_collection、_clean_document、_dump_value、_dump_outline、_dump_sources。
"""