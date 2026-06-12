# 模块 7：Agent 系统 Prompt

## 涉及文件

- `app/agents/prompts/research_manager.md` —— 研究管理智能体 Prompt（约 400 行）
- `app/agents/prompts/search_agent.md` —— 信息检索智能体 Prompt（约 300 行）

---

## 7.1 Prompt 工程模式

Prompt 以外部 `.md` 文件维护，通过 `_load_prompt()` 加载：

```python
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
RESEARCH_MANAGER_PROMPT_PATH = PROMPT_DIR / "research_manager.md"
SEARCH_AGENT_PROMPT_PATH = PROMPT_DIR / "search_agent.md"

def _load_prompt(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8").strip()
```

**好处**：
- Prompt 和代码分离，非开发人员也可以查看和调整
- Markdown 格式天然适合 LLM 阅读（层级结构清晰）
- 版本管理方便，可以单独 diff Prompt 变更

---

## 7.2 ResearchManager Prompt 结构分析

### 职责边界

```
你负责：
  ✅ 设计大纲、协调检索、整理事实、撰写章节、保存结果
  ✅ 理解用户输入（主题、目标、读者、地域、时间）
  ✅ 委托信息检索智能体获取资料

你不负责：
  ❌ 不直接搜索、不读网页、不调 RAGFlow
  ❌ 不写 HTML、不调用报告渲染工具
  ❌ 不输出无法被 JSON 解析的最终结果
```

"不做什么"和"做什么"同样重要。明确的边界防止 Agent 越权操作（比如主 Agent 跳过子 Agent 自己去搜索，或者生成了半成品的 HTML）。

### 任务类型分支

Prompt 通过 `task_name` 字段区分三种任务，每种有不同的输出格式：

**1. generate_research_brief** → 输出 `{research_brief: {...}, outline: [...]}`

要求：
- 大纲必须覆盖：研究定义、边界、现状、驱动因素、竞争、机会、风险、建议
- `node_id` 必须稳定（如 `1`, `1.1`, `2`）
- 不生成章节正文，不生成 HTML

**2. revise_outline** → 输出 `{outline: [...]}`

要求：
- 保留原大纲中仍然合理的部分
- 修改后重新整理 node_id
- 只输出 JSON

**3. generate_report** → 逐章节保存，最终输出 `{saved_sections: [...], status: "sections_saved"}`

这是最复杂的任务。Prompt 要求 Agent：
1. 识别需要写正文的章节（优先叶子节点）
2. 如果 `missing_section_ids` 存在，本轮只处理这些
3. 对每个章节拆解检索问题 → 委托 search-agent → 写出正文 → `save_research_section`
4. 工具返回 `ok=false` 时必须修正后重试
5. 一个章节完成再进入下一个

### 章节保存的 Schema 约束

Prompt 中详细定义了 `save_research_section` 的 section 结构：

```json
{
  "section_id": "2.2.3",
  "title": "章节标题",
  "summary": "本章核心结论",
  "body": "完整正文——由 Agent 基于检索事实完成",
  "key_findings": ["发现1", "发现2"],
  "evidence_chain": [{
    "claim": "可追溯判断",
    "fact_ids": ["fact-1"],
    "source_ids": ["source-1"],
    "confidence": "high"
  }],
  "sources": [{
    "source_id": "source-1",
    "title": "来源标题",
    "url": "https://...",
    "published_at": "2026-01-01",
    "source_type": "public_web",
    "summary": "该来源支持本章节中的关键判断"
  }],
  "tables": [],
  "charts": [],
  "risks": ["不确定性说明"]
}
```

并对每个字段有严格约束：
- `body` 必须是完整正文，不是写作说明
- `evidence_chain.source_ids` 的每个来源必须在 `sources` 中有对应详情
- 不能编造 URL、日期、数据
- 证据不足时必须降低置信度并在 risks 中说明
- **禁止占位文案**（"占位""待生成"等）

### 虚拟文件系统策略

```
建议文件路径：
  /research/workspace/search_questions.json
  /research/workspace/sources.json
  /research/workspace/fact_cards.json
  /research/workspace/conflicts.json
  /research/workspace/insight_cards.json
  /research/workspace/section_research_notes.json
```

Agent 被要求把大规模中间结果写入虚拟文件系统，而不是全部堆在对话上下文里。这能显著减少 token 消耗。

### Few-shot 示例

Prompt 包含两个完整示例：
- 示例 1：生成研究任务书和大纲（展示正确的 JSON 结构和章节设计）
- 示例 2：逐章节保存的工作流（展示如何拆解检索问题、构建 section、调用工具、处理返回值）

---

## 7.3 SearchAgent Prompt 结构分析

### 职责边界

```
你负责：
  ✅ 公开互联网搜索、网页读取、RAGFlow 检索
  ✅ 来源去重和相关性判断
  ✅ 提取可复核事实、识别来源冲突

你不负责：
  ❌ 不设计大纲
  ❌ 不生成 HTML
  ❌ 不编造来源、不把猜测当事实
  ❌ 不保存数据库状态
```

SearchAgent 是纯"资料员"角色——只负责找资料、提取事实、发现冲突。

### 来源类型体系

```python
source_type: public_web | internal_knowledge_base | official_document
           | industry_report | news | unknown
```

不同的 source_type 影响验证规则：`internal_knowledge_base` 不需要 URL，其他类型需要。

### 事实置信度框架

```
high   → 多个可靠来源一致支持
medium → 单个可靠来源支持，或多个来源口径基本一致
low    → 来源不足、时间较旧、口径冲突、只能间接支持
```

这是一个可操作的判断标准，不是简单的"感觉可信"。Agent 需要具体检查来源数量、质量和一致性来定级。

### 冲突信息处理

```json
{
  "conflict_id": "conflict-1",
  "topic": "冲突主题",
  "description": "A 说 X，B 说 Y",
  "source_ids": ["source-1", "source-2"],
  "resolution_suggestion": "建议如何呈现"
}
```

当不同来源口径不一致时（如两个行业报告对市场规模预测不同），不强行合并为一个事实。而是作为"冲突"单独记录，让主 Agent 在撰写章节时决定如何处理。

### Few-shot 示例

Prompt 包含两个示例：
- 示例 1：正常检索（展示工具调用计划和输出格式）
- 示例 2：来源冲突（展示两个口径不一致时如何分别报告）

---

## 7.4 Prompt 设计要点总结

| 方面 | 做法 | 效果 |
|------|------|------|
| 职责边界 | 明确列出"做什么/不做什么" | 防止越权、防止幻觉行为 |
| 输出 Schema | JSON 结构 + 逐字段约束 | 保证产出能被 Pydantic 解析 |
| 禁止规则 | "不能编造""禁止占位""不要 Markdown" | 减少 Agent 偷懒和编造倾向 |
| Few-shot | 完整输入输出示例 | 比自然语言描述更精准地传达期望 |
| 上下文管理 | 虚拟文件系统卸载中间结果 | 控制 token 消耗 |
| 容错指令 | "工具返回 ok=false 时要修正" | 让 Agent 自我纠错而非失败 |
