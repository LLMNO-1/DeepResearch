# 模块 6：Agent 核心 — ResearchAgent 门面

## 涉及文件

- `app/agents/research_agent.py` —— 约 1290 行，项目最长的文件

---

## 6.1 定位：为什么需要一个"门面"

`ResearchAgent` 是 background 层和 DeepAgents 框架之间的隔离层。它的职责：

```
background（胶水层）
    │  只调用 3 个业务方法
    ▼
ResearchAgent（门面）  ← 我们现在在这一层
    │  构建输入、调用框架、解析输出
    ▼
DeepAgents（框架层）
    │  LLM 调用、工具执行、子 Agent 委托
```

如果将来换框架（比如从 DeepAgents 换成 LangGraph 原生 Agent 或 CrewAI），只需要改 `ResearchAgent` 的实现，background 层代码不变。

---

## 6.2 内部数据类型

`ResearchAgent` 内部定义了一套 Pydantic 模型，它们是**Agent 输出的结构化描述**：

### 研究任务书（ResearchBrief）

```python
class ResearchBrief(BaseModel):
    topic: str
    research_goal: str
    target_audience: str
    scope_summary: str
    key_questions: list[str] = []     # 研究要回答的关键问题
    assumptions: list[str] = []        # 研究的默认假设
    success_criteria: list[str] = []   # 成功标准
```

Agent 生成大纲前置产出研究任务书——明确"我们到底要研究什么"。

### 事实卡片（FactCard）

```python
class FactCard(BaseModel):
    fact_id: str
    statement: str               # 可复核的事实陈述
    source_ids: list[str] = []   # 支持此事实的来源编号
    confidence: str = "medium"   # high / medium / low
```

事实卡片是研究过程的核心产出。每个事实都标注了来源和置信度，支持"这个结论是从哪里来的"的追溯。

### 洞察卡片（InsightCard）

```python
class InsightCard(BaseModel):
    insight_id: str
    title: str
    summary: str
    supporting_fact_ids: list[str] = []  # 支持此洞察的事实编号
```

洞察是对多个事实的归纳和判断，比事实高一层抽象。一个洞察对应多条支持性事实。

### 章节（ResearchSection）

```python
class ResearchSection(BaseModel):
    section_id: str
    title: str
    summary: str | None = None
    body: str                           # 章节完整正文
    key_findings: list[str] = []
    evidence_chain: list[EvidenceItem] = []  # 证据链条
    sources: list[ReportSource] = []         # 本章节引用的来源
    tables: list[dict] = []
    charts: list[dict] = []
    risks: list[str] = []                    # 不确定性说明
```

这是 Agent 逐章节产出并保存到数据库的单元。每个章节必须带正文、关键发现、证据链和风险说明。

### 研究综合（ResearchSynthesis）

```python
class ResearchSynthesis(BaseModel):
    executive_summary: str | None = None      # 全局摘要
    core_conclusions: list[str] = []           # 核心结论
    cross_section_insights: list[str] = []     # 跨章节洞察
    strategic_recommendations: list[str] = []   # 战略建议
    global_risks: list[str] = []               # 全局风险
```

在所有章节完成后，从章节内容中确定性聚合出全局综合。**不调用 LLM**——直接从已完成的章节中提取。

### 完整研究结果（ResearchResult）

```python
class ResearchResult(BaseModel):
    title: str
    executive_summary: str | None = None
    sections: list[ResearchSection] = []
    sources: list[ReportSource] = []
    fact_cards: list[FactCard] = []
    insight_cards: list[InsightCard] = []
    synthesis: ResearchSynthesis | None = None
```

这是"研究阶段"的最终产出，是研究和渲染之间的边界对象。渲染层只读取 `ResearchResult`，不调用 LLM 也不访问外部服务。

---

## 6.3 ResearchAgent 的三个公开方法

### 6.3.1 generate_research_brief

```python
async def generate_research_brief(self, project) -> ResearchBriefResult:
    payload = self._build_generate_research_brief_input(project=project)
    raw_result = await self._invoke_manager_agent(
        task_name="generate_research_brief", payload=payload)
    result = self._parse_research_brief_result(raw_result, project)
    return result
```

最简短的 Agent 调用。输入是项目基础信息（主题、目标、范围），输出是研究任务书 + 大纲草案。无循环、无工具调用（大纲阶段不需要搜索）。

### 6.3.2 revise_outline

```python
async def revise_outline(self, project, outline, revision_instruction) -> list[OutlineNode]:
    payload = self._build_revise_outline_input(
        project=project, outline=outline,
        revision_instruction=revision_instruction)
    raw_result = await self._invoke_manager_agent(
        task_name="revise_outline", payload=payload)
    revised_outline = self._parse_outline_result(
        raw_result, fallback_outline=outline)
    return revised_outline
```

输入多了当前大纲和用户修改要求。`fallback_outline` 确保解析失败时至少返回原大纲，不会因为 Agent 输出格式错误导致大纲丢失。

### 6.3.3 generate_research_result（核心方法）

这是整个项目最复杂的方法——**逐章节检索+撰写+落库的循环**：

```python
async def generate_research_result(self, project, outline, user_instruction):
    # 0. 清理旧数据
    await research_project_repository.clear_research_sections(project_id)

    # 1. 确定需要写正文的章节（叶子节点）
    expected_section_ids = self._expected_research_section_ids(outline)

    # 2. 循环：最多 4 次尝试
    missing_section_ids = sorted(expected_section_ids)
    for attempt in range(1, 5):
        # 构建输入（包含 missing_section_ids，告诉 Agent 哪些章节还没写）
        payload = self._build_generate_research_result_input(
            project=project, outline=outline,
            required_section_ids=sorted(expected_section_ids),
            missing_section_ids=missing_section_ids, attempt=attempt)

        # 调用 Agent（Agent 会通过 save_research_section 工具逐章节落库）
        await self._invoke_manager_agent(
            task_name="generate_report", payload=payload)

        # 检查哪些章节已保存
        sections = await research_project_repository.get_research_sections(project_id)
        saved_ids = {s["section_id"] for s in sections if s.get("section_id")}
        missing_section_ids = sorted(expected_section_ids - saved_ids)

        if not missing_section_ids:
            break  # 所有章节都写完了

    # 3. 从已保存章节组装最终结果
    result = self._build_research_result_from_saved_sections(...)
    return result
```

**关键设计**：

1. **逐章节落库**：Agent 不是一次性返回所有章节，而是每写完一个章节就通过 `save_research_section` 工具保存到 MongoDB。如果某次调用中断，已保存的章节不会丢失。

2. **最多 4 轮补写**：LLM 可能在一次性调用中写不完全部章节（输出太长被截断、遗漏某些章节等）。每轮结束后检查 missing_section_ids，告诉 Agent "你还有这些章节没写"，继续下一轮。

3. **叶子节点策略**：只有大纲的叶子节点（没有子章节的节点）需要写正文。父节点是概览型，由渲染层自动生成。

---

## 6.4 DeepAgents 调用封装

### _invoke_manager_agent

```python
async def _invoke_manager_agent(self, task_name, payload):
    if self.manager_agent is None:
        logger.warning("研究管理智能体尚未接入，使用占位结果")
        return self._build_placeholder_result(task_name, payload)

    return await self.manager_agent.ainvoke(
        self._build_deepagents_input(payload),
        config=self._build_deepagents_config(payload))
```

**占位模式**：`manager_agent` 为 `None` 时返回占位数据。这是在系统初始化阶段（API Key 未配置、DeepAgents 构建失败）的降级策略——让前后端可以独立联调，不至于因为 Agent 层缺失而整个系统不可用。

### _build_deepagents_input

```python
def _build_deepagents_input(self, payload):
    task_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "messages": [{
            "role": "user",
            "content": (
                "请执行 /research/task_payload.json 中的研究任务。"
                "先使用 todo 规划步骤；大规模检索结果和报告中间稿"
                "请写入 /research/workspace/ 下的文件；"
                "最终只返回严格 JSON。"
            ),
        }],
        "files": {
            "/research/task_payload.json": create_file_data(task_json),
            "/research/workspace/README.md": create_file_data(
                "该目录用于保存检索摘要、来源整理、事实卡片、洞察卡片和报告草稿。"),
        },
    }
```

**虚拟文件系统**是 DeepAgents 的核心能力。大任务载荷（可能包含完整的 project 数据和 outline 树）不直接放在 messages 里，而是写入虚拟文件 `/research/task_payload.json`。Agent 收到的是一个简短指令 + 文件路径——减少了主上下文的膨胀。

`/research/workspace/` 目录用于 Agent 自主保存中间结果（检索摘要、事实卡片等），避免对话上下文被大量中间数据撑爆。

### _build_deepagents_config

```python
def _build_deepagents_config(self, payload):
    project_id = project.get("project_id") or "default-project"
    task_name = payload.get("task_name") or "research-task"
    return {"configurable": {"thread_id": f"research:{project_id}:{task_name}"}}
```

`thread_id` 确保同一项目的同一个任务使用相同的对话线程。DeepAgents 通过 `MemorySaver` checkpoint 记住之前的对话和虚拟文件系统状态，使得多轮补写可以延续上下文。

---

## 6.5 输出解析层

### 结构化提取的多层兼容

`_parse_xxx_result` 系列方法体现了"宽容解析"模式：

```python
def _parse_research_brief_result(self, raw_result, project):
    if isinstance(raw_result, ResearchBriefResult):
        return raw_result                        # 已是目标类型，直接返回
    raw_data = self._as_dict(raw_result)
    if "research_brief" in raw_data and "outline" in raw_data:
        return ResearchBriefResult.model_validate(raw_data)  # 正确的 dict，校验
    return self._build_placeholder_research_brief_result(project)  # 格式错误，回退占位
```

三层兼容：
1. 如果 Agent 恰好返回了 Pydantic 对象 → 直接用
2. 如果是 dict 且结构匹配 → 校验后使用
3. 如果结构完全不对 → 返回占位数据（不抛异常，不阻塞链路）

### _extract_json_from_messages

```python
def _extract_json_from_messages(self, value):
    messages = value.get("messages")
    if not isinstance(messages, list):
        return {}
    for message in reversed(messages):  # 从最后一条消息开始找
        content = self._extract_message_content(message)
        parsed = self._parse_json_text(content)
        if parsed:
            return parsed
    return {}
```

DeepAgents/LangGraph 的返回结果可能是一个完整的 state dict，JSON 输出藏在最后一条 message 里。这个方法从后往前遍历 messages，找到第一个可解析的 JSON object。

### _parse_json_text

```python
def _parse_json_text(self, text):
    stripped = text.strip()
    # 去掉 ```json ... ``` 包裹
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    # 尝试直接解析
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # 从文本中提取第一个 { ... } 块
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start:end+1])
    return {}
```

处理 LLM 输出的常见问题：
- 模型在 JSON 外加了 ```json 代码块标记
- 模型在 JSON 前后写了说明文字
- 这三层尝试（去标记 → 直接解析 → 提取花括号）覆盖了大多数情况

---

## 6.6 章节校验（_validate_saved_research_sections）

```python
def _validate_saved_research_sections(self, sections):
    placeholder_markers = ["占位", "待生成", "待补充", "真实内容将在", "尚未接入真实"]
    for section in sections:
        section_text = " ".join([
            section.body,
            " ".join(section.key_findings),
            " ".join(item.claim for item in section.evidence_chain),
            " ".join(section.risks),
        ])
        if any(marker in section_text for marker in placeholder_markers):
            raise ValueError(f"章节 {section.section_id} 包含占位内容")
        if not section.body.strip():
            raise ValueError(f"章节 {section.section_id} 缺少正文")
        if not section.key_findings:
            raise ValueError(f"章节 {section.section_id} 缺少关键发现")
        if not section.evidence_chain:
            raise ValueError(f"章节 {section.section_id} 缺少证据链")
        # 检查来源完整性
        section_source_ids = {s.source_id for s in section.sources if s.source_id}
        evidence_source_ids = {...}
        missing = evidence_source_ids - section_source_ids
        if missing:
            raise ValueError(f"章节 {section.section_id} 缺少来源详情: {missing}")
        # 检查公开来源必须有 URL
        for source in section.sources:
            if source.source_type != "internal_knowledge_base" and not http_url(source.url):
                raise ValueError(f"公开来源 {source.source_id} 缺少 URL")
```

这是研究质量的最后一道防线。每一章必须满足：
- 正文 ≥ 120 字（在 `save_research_section` 工具中检查）
- 不含占位文案
- 有关键发现
- 有证据链
- 证据链引用的所有来源都有对应的来源详情
- 公开来源必须有可访问的 URL

任一条件不满足，整个研究结果被拒绝，不会进入渲染流程。

---

## 6.7 Agent 构建

### 单例模式

```python
_research_agent: ResearchAgent | None = None

def get_research_agent():
    global _research_agent
    if _research_agent is None:
        _research_agent = build_research_agent()
    return _research_agent
```

进程内只有一个 ResearchAgent 实例，避免重复创建 DeepAgents agent 对象和 MemorySaver。

### _build_deepagents_manager_agent

```python
def _build_deepagents_manager_agent():
    settings = get_settings()
    model_name = _build_model_name(settings)  # "openai:gpt-4.1-mini" 或 "deepseek:xxx"
    subagents = [_build_search_subagent(model_name=model_name)]
    return create_deep_agent(
        model=model_name,
        tools=[save_research_section],       # 主 Agent 只有一个工具
        system_prompt=_load_prompt(RESEARCH_MANAGER_PROMPT_PATH),
        subagents=subagents,                 # 一个子 Agent：search-agent
        checkpointer=MemorySaver(),
    )
```

**主 Agent 只有一个直接工具**：`save_research_section`。搜索能力通过子 Agent 委托实现。这是职责分离——主 Agent 负责规划和写作，子 Agent 负责搜索。

### 模型名称构建

```python
def _build_model_name(settings):
    provider = settings.llm_provider.lower()  # "openai" 或 "deepseek"
    if provider == "deepseek":
        return f"deepseek:{settings.llm_model_name}"
    if provider == "openai":
        return f"openai:{settings.llm_model_name}"
    return f"{provider}:{settings.llm_model_name}"
```

DeepAgents/LangChain 的模型名称格式是 `provider:model_name`，不是简单的模型名。

### 子 Agent 构建

```python
def _build_search_subagent(model_name):
    settings = get_settings()
    if settings.enable_ragflow:
        tools = [external_search, read_web_page, ragflow_search]
    else:
        tools = [external_search, read_web_page]

    return {
        "name": "search-agent",
        "description": "负责公开互联网检索、网页读取、RAGFlow 内部知识库检索和证据整理。",
        "system_prompt": _load_prompt(SEARCH_AGENT_PROMPT_PATH),
        "tools": tools,
        "model": model_name,
    }
```

子 Agent 的工具集根据配置动态组装：如果启用了 RAGFlow，子 Agent 就有 3 个工具；否则只有 2 个（公开搜索 + 网页读取）。

---

## 6.8 确定性聚合方法

Agent 写完所有章节后，`ResearchAgent` 内部用纯代码（不调 LLM）从章节中聚合出全局数据：

```python
def _build_fact_cards_from_sections(self, sections):
    # 从章节证据链中提取所有 fact_id → 建立去重映射
    cards: dict[str, FactCard] = {}
    for section in sections:
        for evidence in section.evidence_chain:
            for fact_id in evidence.fact_ids:
                cards[fact_id] = FactCard(
                    fact_id=fact_id, statement=evidence.claim,
                    source_ids=evidence.source_ids, confidence=evidence.confidence)
    return list(cards.values())

def _build_synthesis_from_sections(self, sections):
    # 从每个章节的 summary + key_findings 中提取全局综合
    core_conclusions = []
    for section in sections:
        if section.summary:
            core_conclusions.append(section.summary)
        core_conclusions.extend(section.key_findings[:2])
    # ...
    return ResearchSynthesis(executive_summary=..., core_conclusions=...)
```

这种"确定性聚合"比让 LLM 总结更可靠——不会有幻觉，结果完全可追溯。
