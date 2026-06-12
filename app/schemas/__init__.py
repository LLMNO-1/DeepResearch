"""
该模块用于定义所有接口的 schema 信息。

按类型分为五组：
  1. 枚举 —— 项目状态、任务状态、地域范围等固定值域
  2. 内部结构 —— 被请求/响应模型共享引用的嵌套模型
  3. 请求模型 —— 前端 → 后端的入参校验
  4. 响应模型 —— 后端 → 前端的出参结构
  5. 工具函数
"""
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


# =============================================================================
# 1. 枚举类型 —— 固定值域，约束字段可选范围
# =============================================================================

class ProjectStatus(StrEnum):
    """研究项目生命周期状态。

    状态流转：
    CREATED → BRIEF_GENERATING → OUTLINE_READY → OUTLINE_REVISING
                                         ↓
                                  OUTLINE_CONFIRMED → RESEARCH_RUNNING → REPORT_READY → COMPLETED
    """

    CREATED = "created"
    BRIEF_GENERATING = "brief_generating"
    OUTLINE_READY = "outline_ready"
    OUTLINE_REVISING = "outline_revising"
    OUTLINE_CONFIRMED = "outline_confirmed"
    RESEARCH_RUNNING = "research_running"
    REPORT_READY = "report_ready"
    COMPLETED = "completed"


class TaskStatus(StrEnum):
    """后台任务执行状态，用于前端轮询判断任务是否完成。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskType(StrEnum):
    """后台任务类型，区分不同的 Agent 执行阶段。"""

    GENERATE_RESEARCH_BRIEF = "generate_research_brief"  # 生成研究任务书 + 大纲
    REVISE_OUTLINE = "revise_outline"                    # 按用户要求修改大纲
    GENERATE_REPORT = "generate_report"                  # 研究 + 渲染报告
    RENDER_REPORT = "render_report"                      # 仅渲染（不重新研究）


class RegionScope(StrEnum):
    """研究地域范围。"""

    CHINA = "china"
    OVERSEAS = "overseas"
    GLOBAL = "global"


class TimeScopeType(StrEnum):
    """研究时间范围类型。"""

    RECENT_YEARS = "recent_years"  # 限定最近 N 年
    UNLIMITED = "unlimited"        # 不限制时间


class OutlineAction(StrEnum):
    """大纲操作类型，用于 PUT /outline 接口区分确认和修改。"""

    CONFIRM = "confirm"
    REVISE = "revise"


class NextStep(StrEnum):
    """下一步操作提示，告诉前端当前应该展示什么按钮。

    例如 NextStep.GENERATE_REPORT 意味着前端可以显示"生成报告"按钮。
    """

    WAIT_FOR_OUTLINE = "wait_for_outline"
    GENERATE_REPORT = "generate_report"
    WAIT_FOR_REPORT = "wait_for_report"


# =============================================================================
# 2. 内部结构（复合模型） —— 被请求/响应模型共享引用，不直接对应 API 端点
# =============================================================================

class TimeScope(BaseModel):
    """研究时间范围，内嵌在 ResearchProjectCreate 中使用。

    校验规则：type=recent_years 时 years 必填且范围 1-20。
    """

    type: TimeScopeType
    years: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def validate_years(self) -> "TimeScope":
        if self.type == TimeScopeType.RECENT_YEARS and self.years is None:
            raise ValueError("time_scope.type 为 recent_years 时必须提供 years")
        return self


class OutlineNode(BaseModel):
    """研究大纲节点，支持递归嵌套（children 可包含子节点）。

    叶子节点（无 children）对应需要撰写正文的章节；
    父节点（有 children）仅做分组，渲染时显示为概览型章节。
    """

    node_id: str
    title: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=500)
    children: list["OutlineNode"] = Field(default_factory=list)


class ReportSource(BaseModel):
    """报告参考来源，记录可追溯的公开网页或内部知识库引用。

    同时用于：
    - Agent 落库的 sections[].sources
    - research_result.sources
    - LatestReportResponse.sources
    """

    source_id: str | None = None
    title: str
    url: str | None = None
    published_at: str | None = None
    source_type: str


# =============================================================================
# 3. 请求模型 —— 前端 → 后端入参校验
# =============================================================================

class ResearchProjectCreate(BaseModel):
    """创建研究项目请求体。

    对应 POST /research-projects。
    """

    topic: str = Field(min_length=2, max_length=200)
    research_goal: str = Field(min_length=2, max_length=500)
    target_audience: str = Field(min_length=2, max_length=100)
    region_scope: RegionScope
    time_scope: TimeScope


class OutlineUpdateRequest(BaseModel):
    """保存大纲请求体，支持确认或提交修改指令。

    对应 PUT /research-projects/{id}/outline。
    action=confirm 时直接确认，不需 revision_instruction；
    action=revise 时必须提供 revision_instruction。
    """

    action: OutlineAction
    revision_instruction: str | None = Field(default=None, min_length=2, max_length=1000)

    @model_validator(mode="after")
    def validate_revision_instruction(self) -> "OutlineUpdateRequest":
        if self.action == OutlineAction.REVISE and not self.revision_instruction:
            raise ValueError("action 为 revise 时必须提供 revision_instruction")
        return self


class ReportTaskCreate(BaseModel):
    """创建报告生成任务请求体，user_instruction 为可选的补充要求。

    对应 POST /research-projects/{id}/report-tasks
    和 POST /research-projects/{id}/report-render-tasks。
    """

    user_instruction: str | None = Field(default=None, max_length=1000)


# =============================================================================
# 4. 响应模型 —— 后端 → 前端出参结构
# =============================================================================

class ResearchProjectCreateResponse(BaseModel):
    """创建研究项目响应体。

    前端拿到 initial_task_id 后开始轮询 GET /tasks/{id}。
    """

    project_id: str
    initial_task_id: str
    initial_task_type: TaskType
    topic: str
    status: ProjectStatus
    next_step: NextStep
    created_at: datetime


class OutlineResponse(BaseModel):
    """获取大纲响应体。

    对应 GET /research-projects/{id}/outline。
    """

    project_id: str
    status: ProjectStatus
    outline: list[OutlineNode]


class OutlineConfirmResponse(BaseModel):
    """大纲确认成功响应体。

    PUT /outline action=confirm → 前端可继续到"生成报告"。
    """

    project_id: str
    status: ProjectStatus
    next_step: NextStep


class OutlineRevisionResponse(BaseModel):
    """大纲修改任务创建响应体。

    PUT /outline action=revise → 前端拿到 revision_task_id 开始轮询。
    """

    project_id: str
    revision_task_id: str
    status: ProjectStatus
    next_step: NextStep


class ReportTaskCreateResponse(BaseModel):
    """报告生成/渲染任务创建响应体。

    对应 POST /report-tasks 和 POST /report-render-tasks。
    """

    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus


class TaskStatusResponse(BaseModel):
    """后台任务状态查询响应体。

    对应 GET /tasks/{task_id}，前端每 2 秒轮询一次。
    """

    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus
    message: str
    created_at: datetime
    updated_at: datetime


class LatestReportResponse(BaseModel):
    """最新报告响应体。

    对应 GET /research-projects/{id}/reports/latest。
    html 字段在报告渲染阶段由确定性 Python 代码生成，不经过 LLM。
    """

    project_id: str
    report_id: str
    version: int
    title: str
    html: str
    sources: list[ReportSource]
    created_at: datetime


# =============================================================================
# 5. 工具函数
# =============================================================================

def utc_now() -> datetime:
    """返回 UTC 当前时间，统一所有接口和任务状态中的时间格式。"""

    return datetime.now(timezone.utc)
"""
 schemas/__init__.py
  │
  ├── 1. 枚举类型 (7 个)
  │   ├── ProjectStatus      — 项目生命周期状态 (8 个状态)
  │   ├── TaskStatus         — 后台任务状态 (4 个)
  │   ├── TaskType           — 任务类型 (4 种)
  │   ├── RegionScope        — 地域范围 (3 个)
  │   ├── TimeScopeType      — 时间范围类型 (2 个)
  │   ├── OutlineAction      — 大纲操作 (2 个)
  │   └── NextStep           — 下一步提示 (3 个)
  │
  ├── 2. 内部结构/复合模型 (3 个)
  │   ├── TimeScope          — 时间范围（含 validator）
  │   ├── OutlineNode        — 大纲节点（递归嵌套）
  │   └── ReportSource       — 报告来源（跨模块复用）
  │
  ├── 3. 请求模型 (3 个)
  │   ├── ResearchProjectCreate   — POST /research-projects
  │   ├── OutlineUpdateRequest    — PUT /outline（含 validator）
  │   └── ReportTaskCreate        — POST /report-tasks
  │
  ├── 4. 响应模型 (7 个)
  │   ├── ResearchProjectCreateResponse — POST /research-projects 返回
  │   ├── OutlineResponse               — GET /outline 返回
  │   ├── OutlineConfirmResponse        — PUT /outline confirm 返回
  │   ├── OutlineRevisionResponse       — PUT /outline revise 返回
  │   ├── ReportTaskCreateResponse      — POST /report-tasks 返回
  │   ├── TaskStatusResponse            — GET /tasks/{id} 轮询返回
  │   └── LatestReportResponse          — GET /reports/latest 返回
  │
  └── 5. 工具函数 (1 个)
      └── utc_now() — UTC 时间戳


"""