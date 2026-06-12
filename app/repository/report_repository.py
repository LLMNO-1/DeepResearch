"""报告版本数据访问层。

按报告生命周期顺序排列：
  保存版本 → 读取最新版本

内部辅助函数统一放在文件末尾。
"""
from typing import Any
from uuid import uuid4

from app.repository.mongodb import get_mongodb_database
from app.repository.report_storage import get_report_object_storage
from app.schemas import LatestReportResponse, ReportSource, utc_now

COLLECTION_NAME = "report_versions"


# =============================================================================
# 1. 保存报告版本 —— 后台任务研究+渲染完成后调用
# =============================================================================

async def save_report_version(
    project_id: str,
    title: str,
    html: str,
    sources: list[ReportSource] | list[dict[str, Any]],
) -> LatestReportResponse:
    """保存一份新的报告版本，版本号自增。

    调用方：background/research_tasks.py _run_generate_report_task() 和 _run_render_report_task()
    时机：研究执行完毕且确定性渲染完成后
    写入策略：
    - HTML 正文存入对象存储（local 文件 / MinIO），MongoDB 只存 html_uri
    - 来源列表和元数据存 MongoDB
    - 版本号从当前最大版本 +1，首次为 1
    """

    latest = await _get_collection().find_one(
        {"project_id": project_id},
        sort=[("version", -1)],
        projection={"version": 1},
    )
    next_version = int(latest["version"]) + 1 if latest else 1
    created_at = utc_now()
    report_id = str(uuid4())

    # HTML 正文写入对象存储（文件系统或 MinIO），MongoDB 只存 uri
    stored = await get_report_object_storage().save_html(
        project_id=project_id,
        report_id=report_id,
        version=next_version,
        html=html,
    )

    report = LatestReportResponse(
        project_id=project_id,
        report_id=report_id,
        version=next_version,
        title=title,
        html=html,
        sources=[ReportSource.model_validate(s) for s in _dump_sources(sources)],
        created_at=created_at,
    )
    document = report.model_dump(mode="python", exclude={"html"})
    document["_id"] = report.report_id
    document["html_uri"] = stored.uri
    document["html_path"] = stored.path
    document["html_size"] = stored.size
    await _get_collection().insert_one(document)
    return report


# =============================================================================
# 2. 读取最新报告 —— 前端展示 / 导出时调用
# =============================================================================

async def get_latest_report(project_id: str) -> LatestReportResponse | None:
    """读取项目的最新报告版本。

    调用方：routers/__init__.py get_latest_report() → GET /reports/latest
    时机：前端轮询到任务成功后调用，或用户从"历史报告" Tab 查看
    读取策略：按 version 降序取第一条，MongoDB 如果存了 html_uri 则从对象存储加载 HTML，
            否则回退读 MongoDB 内的 html 字段（兼容旧版本）。
    """

    document = await _get_collection().find_one(
        {"project_id": project_id},
        sort=[("version", -1)],
    )
    return await _report_from_document(document)


# =============================================================================
# 内部辅助函数
# =============================================================================

def _get_collection():
    """获取 MongoDB report_versions 集合对象。"""
    return get_mongodb_database()[COLLECTION_NAME]


def _dump_sources(sources: list[ReportSource] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ReportSource 列表 → 可写入 MongoDB 的 dict 列表。"""
    dumped: list[dict[str, Any]] = []
    for source in sources:
        if isinstance(source, ReportSource):
            dumped.append(source.model_dump(mode="python"))
        else:
            dumped.append(source)
    return dumped


async def _report_from_document(document: dict[str, Any] | None) -> LatestReportResponse | None:
    """MongoDB 文档 → LatestReportResponse。

    HTML 优先从对象存储（html_uri）加载，兼容旧版 MongoDB 内嵌 html 字段。
    来源列表从 dict 反序列化为 ReportSource Pydantic 对象。
    """

    if document is None:
        return None
    html = await _load_report_html(document=document)
    return LatestReportResponse(
        project_id=str(document["project_id"]),
        report_id=str(document["report_id"]),
        version=int(document["version"]),
        title=str(document["title"]),
        html=html,
        sources=[ReportSource.model_validate(source) for source in document.get("sources", [])],
        created_at=document["created_at"],
    )


async def _load_report_html(document: dict[str, Any]) -> str:
    """从对象存储或 MongoDB 内嵌字段加载 HTML。

    优先读 html_uri → 调用对象存储 read_html()；
    回退读 html 字段 → 兼容旧版本数据。
    """

    html_uri = document.get("html_uri")
    if isinstance(html_uri, str) and html_uri.strip():
        return await get_report_object_storage().read_html(uri=html_uri)
    return str(document.get("html") or "")

"""

  ┌─────────────────────┬────────────────────────────────────────────────────────┬──────────┐
  │        方法         │                         调用方                         │ 调用次数 │
  ├─────────────────────┼────────────────────────────────────────────────────────┼──────────┤
  │ save_report_version │ background 两个任务（generate_report + render_report） │ 2 处     │
  ├─────────────────────┼────────────────────────────────────────────────────────┼──────────┤
  │ get_latest_report   │ routers GET /reports/latest                            │ 1 处     │
  └─────────────────────┴────────────────────────────────────────────────────────┴──────────┘

  4 个内部辅助：_get_collection、_dump_sources、_report_from_document、_load_report_html。没有废方法。
"""