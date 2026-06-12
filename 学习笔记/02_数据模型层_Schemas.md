# 模块 2：数据模型层 — Schemas

## 涉及文件

- `app/schemas/__init__.py` —— 所有接口请求/响应模型和枚举定义

本模块约 210 行，是系统最底层的数据契约。所有模块（路由、repository、background、agent）都依赖这里的类型定义。

---

## 2.1 枚举体系

### 项目生命周期状态（ProjectStatus）

```python
class ProjectStatus(StrEnum):
    CREATED           = "created"            # 项目已创建，等待生成研究任务书
    BRIEF_GENERATING  = "brief_generating"   # 正在生成研究任务书和大纲
    OUTLINE_READY     = "outline_ready"      # 大纲已生成，等待用户确认
    OUTLINE_REVISING  = "outline_revising"   # 正在根据用户要求修改大纲
    OUTLINE_CONFIRMED = "outline_confirmed"  # 用户已确认大纲，可以生成报告
    RESEARCH_RUNNING  = "research_running"    # 正在执行研究和报告渲染
    REPORT_READY      = "report_ready"        # 研究报告已生成（可能还需要最终确认）
    COMPLETED         = "completed"           # 项目完成（当前版本未实际使用）
```

状态流转图：

```
CREATED → BRIEF_GENERATING → OUTLINE_READY
                                   ↓
                        用户确认? ──→ OUTLINE_CONFIRMED → RESEARCH_RUNNING → REPORT_READY
                                   ↓
                        用户修改? ──→ OUTLINE_REVISING → OUTLINE_READY (循环)
```

**设计要点**：
- 7 个状态覆盖了从创建到完成的完整路径
- `COMPLETED` 状态已定义但当前代码中未使用——预留扩展点
- 强制"大纲确认"作为人工检查点：用户必须看过/修改过大纲才能触发报告生成，防止 AI 自由发挥后直接出报告

### 后台任务状态（TaskStatus）

```python
class TaskStatus(StrEnum):
    QUEUED    = "queued"     # 已创建，等待执行
    RUNNING   = "running"    # 正在执行
    SUCCEEDED = "succeeded"  # 执行成功
    FAILED    = "failed"     # 执行失败
```

典型的异步任务状态机。前端通过轮询 `GET /tasks/{task_id}` 获取进度，状态从 `queued` → `running` → `succeeded/failed`。

### 任务类型（TaskType）

```python
class TaskType(StrEnum):
    GENERATE_RESEARCH_BRIEF = "generate_research_brief"  # 生成研究任务书+大纲
    REVISE_OUTLINE          = "revise_outline"            # 修改大纲
    GENERATE_REPORT         = "generate_report"           # 研究+报告渲染（二合一）
    RENDER_REPORT           = "render_report"             # 仅渲染（不重新研究）
```

### 大纲操作（OutlineAction）

```python
class OutlineAction(StrEnum):
    CONFIRM = "confirm"  # 确认大纲，开始研究
    REVISE  = "revise"   # 提交修改意见，重新生成大纲
```

### 下一步提示（NextStep）

```python
class NextStep(StrEnum):
    WAIT_FOR_OUTLINE = "wait_for_outline"  # 等待大纲生成
    GENERATE_REPORT  = "generate_report"   # 可以生成报告了
    WAIT_FOR_REPORT  = "wait_for_report"   # 等待报告生成
```

这是路由层的"流程导航"，告诉前端当前应该展示什么操作按钮。例如 `NextStep.GENERATE_REPORT` 意味着前端可以显示"生成报告"按钮。

---

## 2.2 请求模型

### 创建研究项目（ResearchProjectCreate）

```python
class ResearchProjectCreate(BaseModel):
    topic: str            = Field(min_length=2, max_length=200)   # 研究主题
    research_goal: str    = Field(min_length=2, max_length=500)   # 研究目标
    target_audience: str  = Field(min_length=2, max_length=100)   # 目标读者
    region_scope: RegionScope   # china / overseas / global
    time_scope: TimeScope       # 时间范围（近年/不限）
```

**输入校验策略**：pydantic 的 `Field(min_length=..., max_length=...)` 在请求进入路由函数之前就完成校验。如果 `topic` 为空或超过 200 字符，FastAPI 自动返回 422 错误，路由函数根本不会执行。

### 时间范围（TimeScope）

```python
class TimeScope(BaseModel):
    type: TimeScopeType   # recent_years / unlimited
    years: int | None = Field(default=None, ge=1, le=20)

    @model_validator(mode="after")
    def validate_years(self) -> "TimeScope":
        if self.type == TimeScopeType.RECENT_YEARS and self.years is None:
            raise ValueError("time_scope.type 为 recent_years 时必须提供 years")
        return self
```

**`model_validator(mode="after")` 的作用**：在 pydantic 完成字段级校验后，做跨字段的联动校验。这里的意思是"选了 recent_years 就必须填 years，选了 unlimited 则不需要"。

这种校验放在 model 层而不是路由层的好处：无论谁构造 `TimeScope`（API 请求、测试代码、内部调用），校验都会生效。

### 大纲更新请求（OutlineUpdateRequest）

```python
class OutlineUpdateRequest(BaseModel):
    action: OutlineAction
    revision_instruction: str | None = Field(default=None, min_length=2, max_length=1000)

    @model_validator(mode="after")
    def validate_revision_instruction(self) -> "OutlineUpdateRequest":
        if self.action == OutlineAction.REVISE and not self.revision_instruction:
            raise ValueError("action 为 revise 时必须提供 revision_instruction")
        return self
```

同样是跨字段校验：确认大纲不需要修改说明，修改大纲则必须提供。

### 报告任务创建（ReportTaskCreate）

```python
class ReportTaskCreate(BaseModel):
    user_instruction: str | None = Field(default=None, max_length=1000)
```

`user_instruction` 是可选的补充要求（如"重点关注财务数据"、"用英文生成"），最长 1000 字符。传给 Agent 作为额外指示。

---

## 2.3 响应模型

响应模型都继承 `BaseModel`，FastAPI 会在返回前自动校验输出是否符合模型定义（`response_model=xxx`）。

### 创建项目响应

```python
class ResearchProjectCreateResponse(BaseModel):
    project_id: str
    initial_task_id: str
    initial_task_type: TaskType
    topic: str
    status: ProjectStatus
    next_step: NextStep
    created_at: datetime
```

创建完项目后，前端拿到的不是"项目创建成功"这种模糊消息，而是精确的下一步操作指引（`next_step`）。

### 任务状态响应

```python
class TaskStatusResponse(BaseModel):
    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus
    message: str           # 人类可读的状态说明
    created_at: datetime
    updated_at: datetime
```

这是前端轮询的响应模型。`message` 字段在不同阶段内容不同：
- running: "正在生成研究任务书和大纲"
- succeeded: "研究任务书和大纲已生成，等待用户确认"
- failed: "研究任务书和大纲生成失败: ValueError: ..."

### 大纲响应（OutlineResponse）

```python
class OutlineResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    outline: list[OutlineNode]   # 核心！
```

---

## 2.4 核心数据结构：OutlineNode

```python
class OutlineNode(BaseModel):
    node_id: str                              # "1", "1.1", "2.2.3"
    title: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=300)    # 本章要回答的问题
    description: str = Field(min_length=1, max_length=500) # 写作说明
    children: list["OutlineNode"] = Field(default_factory=list)  # 递归！
```

**这是一个递归结构**：`children` 的类型是 `list["OutlineNode"]`，引用自身。这使得大纲可以表达无限层级：

```
1. 定义、边界和研究框架
├── 1.1 概念定义
├── 1.2 产业链范围
└── 1.3 研究框架
2. 政策、基础设施和市场需求
├── 2.1 政策环境
│   ├── 2.1.1 国家政策
│   └── 2.1.2 地方政策
└── 2.2 市场需求
```

每个节点有三个核心字段，语义各不相同：
- `title`：给用户看的章节标题
- `question`：Agent 写这一章时需要回答的问题（驱动研究方向）
- `description`：写作指导（告诉 Agent 这一章应该覆盖什么内容）

**叶子节点的识别**：研究执行时，系统通过 `children` 是否为空判断哪些节点需要真正写正文。父节点（如"1. 定义、边界和研究框架"）是概览型，渲染时只显示引言；叶子节点（如"1.1 概念定义"）是分析型，渲染完整正文+证据链+关键发现。

### 报告来源（ReportSource）

```python
class ReportSource(BaseModel):
    source_id: str | None = None
    title: str
    url: str | None = None
    published_at: str | None = None
    source_type: str     # public_web / internal_knowledge_base / official_document ...
```

`source_id` 是证据链引用的关键。Agent 在写 `evidence_chain` 时引用 `source-1`、`source-2`，渲染时通过 `source_id` 关联到具体来源条目。

---

## 2.5 时间工具函数

```python
def utc_now() -> datetime:
    return datetime.now(timezone.utc)
```

全项目统一使用 UTC 时间。每次需要 `created_at` / `updated_at` 时都调用 `utc_now()`，避免各模块各自 `datetime.now()` 导致时区不一致。

---

## 2.6 设计模式总结

| 模式 | 应用 | 好处 |
|------|------|------|
| StrEnum | 所有枚举 | 字符串可直接序列化到 JSON/MongoDB，同时有类型检查 |
| model_validator(mode="after") | TimeScope, OutlineUpdateRequest | 跨字段联动校验，一个请求内多字段间的约束 |
| Field(min_length, max_length) | 所有请求字段 | 声明式校验，在路由函数执行前拦截 |
| 递归模型 | OutlineNode | 表达树形结构，pydantic 自动递归校验 |
| response_model | 所有路由 | FastAPI 自动校验输出+生成 OpenAPI schema |
| 单一时间源 | utc_now() | 全项目 UTC，避免时区混乱 |

---

## 2.7 与 Agent 层模型的区别

`app/schemas/` 和 `app/agents/research_agent.py` 中各有一组 Pydantic 模型，它们的定位不同：

| 位置 | 用途 | 例子 |
|------|------|------|
| `app/schemas/` | API 层的请求/响应契约 | `ResearchProjectCreate`, `OutlineResponse` |
| `app/agents/research_agent.py` | Agent 内部结构化输出 | `ResearchBrief`, `FactCard`, `ResearchSection` |

Agent 层的模型更"内部"——它们描述了 Agent 输出数据的内容结构（研究任务书、事实卡片、章节正文），可能不会直接暴露给 API 响应。而 schemas 层的模型是前端和后端之间的"接口合同"。
