# DeepResearch —— 企业级 AI 深度研究报告工作台

## 项目定位与难度

| 维度 | 说明 |
|---|---|
| **技术难度** | ★★★★☆（中高级） |
| **涉及领域** | FastAPI 异步架构、MongoDB 文档数据库、LangGraph/DeepAgents 多智能体协作、Prompt Engineering、工具校验与自我修正闭环、虚拟文件系统上下文卸载、确定性渲染引擎（0 LLM 调用）、前端 SPA |
| **代码规模** | ~5000 行 Python + 789 行前端单页面 |
| **适合岗位** | AI 应用开发工程师、Python 后端开发（Agent 方向）、AI 产品技术负责人 |
| **参考薪资** | 25K–45K（北京/上海，2026 年 AI Agent 方向） |

核心看点：**不是简单的 LLM API 调用封装，而是一个完整的多智能体协作系统**——状态机管理项目生命周期、Agent 分工协作（主智能体规划撰写 + 检索智能体搜索整理）、逐章节落库 + 重试补写、研究与渲染彻底分离。

---

## 项目概述

企业内部 AI 研究报告工作台后端服务。用户提交研究主题 → 系统通过 DeepAgents 多智能体协作完成资料检索、事实整理、章节撰写 → 最终渲染为结构化 HTML 研究报告。

与普通 AI 聊天机器人的核心区别：

| 对比项 | 普通 ChatGPT/DeepSeek | 本项目 |
|--------|----------------------|--------|
| 核心对象 | 对话消息 | 研究项目 |
| 用户输入 | 即时问题 | 研究主题 + 研究设定 |
| 执行过程 | 单次生成回答 | 生成大纲 → 确认大纲 → 执行研究 → 渲染报告 |
| 状态管理 | 对话历史 | 项目状态机 + 任务状态机 + 报告版本管理 |
| 信息来源 | 模型知识或临时检索 | 外部公开资料（Tavily）+ 内部知识库（RAGFlow） |
| 输出结果 | 一段回答 | HTML 报告 + 事实卡片 + 洞察卡片 + 可追溯引用 |
| 可靠性 | 用户自行判断 | 结论可通过证据链追溯到来源 |

### 为什么能生成比普通 AI 对话长得多的报告

1. **逐章节拆分执行**：不是一次性生成全文，而是按大纲拆成独立章节，每章"先搜索 → 再整理 → 再写 → 再保存"，循环处理
2. **虚拟文件系统卸载上下文**：大规模检索中间结果存入 `/research/workspace/` 文件，不占用对话上下文窗口
3. **SearchAgent 专门搜索 + ManagerAgent 专门撰写**：检索和写作分离，各司其职，避免上下文混乱
4. **最多重试 4 轮**：每轮完事后查 MongoDB，看哪些章节还没保存，下轮补写——相当于把一个大报告拆成多次小任务逐个完成
5. **确定性渲染不占 LLM 配额**：HTML 由 Python 代码从结构化数据生成，不消耗模型 token

---

## 技术栈

- **Python 3.12+**，包管理器 **uv**（`pyproject.toml` + `uv.lock`）
- **FastAPI** REST API + **MongoDB**（pymongo AsyncMongoClient 异步驱动）
- **DeepAgents**（基于 LangGraph）+ **MemorySaver** checkpoint
- **Tavily API** 公开互联网搜索，**RAGFlow** 内部知识库
- **markdown-it-py** Markdown → HTML 渲染
- 前端 `static/index.html` 单页面，纯 vanilla JS，Tab 切换，无前端框架

---

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
uv sync
uv sync --group dev

# 复制配置文件
cp .env.example .env  # 按需修改 LLM Provider、API Key、MongoDB 地址等
```

### 2. 启动 MongoDB（如用虚拟机已有实例则跳过）

```bash
# 有鉴权部署
docker build -f mongo/Dockerfile.mongodb -t deep-research-mongo:7.0 mongo
docker run -d --name deep-research-mongo -p 27017:27017 \
  -v deep-research-mongo-data:/data/db deep-research-mongo:7.0
# 默认应用用户: deepresearch / deepresearch_dev，数据库: deep_research
# 连接串: mongodb://deepresearch:deepresearch_dev@<host>:27017/deep_research?authSource=deep_research
```

### 3. 启动 RAGFlow（可选）

```bash
docker compose -f ragflow-docker/docker-compose.yml up -d
```

### 4. 启动开发服务器

```bash
# Windows PowerShell（必须前置 PYTHONIOENCODING）
$env:PYTHONIOENCODING='utf-8'; uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Windows CMD
set PYTHONIOENCODING=utf-8 && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# macOS / Linux
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. 打开前端

浏览器访问 `http://localhost:8000` → 创建研究项目 → 等待大纲生成 → 确认大纲 → 生成报告 → 查看报告 → 导出 Markdown/HTML。

---

## 架构分层

```
app/
├── main.py              # FastAPI 应用工厂，挂载路由和静态文件
├── config/config.py     # pydantic-settings 配置，含 _export_to_env() 桥接
├── routers/__init__.py  # 10 个 API 端点，含项目列表、报告导出
├── background/          # asyncio.create_task 后台异步任务调度
├── agents/              # DeepAgents 智能体门面 + 系统 Prompt（Markdown 文件）
│   └── prompts/         # research_manager.md + search_agent.md
├── tools/               # 工具函数：Tavily 搜索、网页读取、RAGFlow、章节落库、报告渲染
├── repository/          # MongoDB 数据访问层 + 对象存储抽象
└── schemas/             # Pydantic 数据模型和 7 个状态枚举
```

## 核心流程

### 研究项目生命周期（状态机）

```
[*] → created → brief_generating → outline_ready → outline_confirmed
       → research_running → report_ready → completed
```

1. **创建项目** `POST /api/v1/research-projects` → 状态 `BRIEF_GENERATING`，后台异步生成研究任务书和大纲
2. **大纲确认** 用户通过 `PUT /outline` 确认（`CONFIRM`）或修改（`REVISE`）大纲
3. **报告生成** `POST /report-tasks` → 状态 `RESEARCH_RUNNING`，后台双阶段执行（研究 + 渲染）
4. **报告渲染** `POST /report-render-tasks` → 基于已落库 research_result 重新渲染 HTML（不重新研究）
5. **获取报告** `GET /reports/latest` → 返回最新 HTML 报告
6. **导出报告** `GET /reports/export?format=md|html` → 文件下载

前端通过 `GET /tasks/{task_id}` 每 2 秒轮询后台任务进度。

### 智能体协作模型

| 智能体 | Prompt | 职责 | 不做什么 |
|--------|--------|------|---------|
| **ResearchManagerAgent** | `research_manager.md` | 大纲设计、检索问题拆解、委托检索子智能体、整理事实/洞察、撰写章节正文、逐章节落库 | 不直接搜索、不生成 HTML |
| **SearchAgent** | `search_agent.md` | 公开搜索（Tavily）、网页读取、RAGFlow 内部检索、事实提取和冲突识别 | 不写报告正文 |

### 研究与渲染分离（核心设计原则）

> LLM 负责理解问题、拆解问题、整理事实和形成洞察；确定性代码负责接口、状态、数据保存和报告渲染。

- **研究阶段**：manager_agent 协调 search_agent 检索 → 撰写章节正文+证据链 → 通过 `save_research_section` 逐章节保存到 MongoDB
- **渲染阶段**：`report_writer.py` 纯确定性 Python 代码，读取已落库 research_result → Document IR → HTML/Markdown。**不允许新增事实或调用 LLM**

为什么不让 LLM 渲染？渲染阶段新增事实会导致来源和证据链断裂；改写已有结论会造成前后不一致；HTML/CSS/目录/引用排版不需要 LLM 推理。

### 后台任务机制

所有长耗时任务通过 `asyncio.create_task` 提交到当前事件循环，不依赖 Celery/Redis 队列。路由层立即返回 `task_id`，前端轮询状态。当前版本适用于单机部署，后续可迁移到 Celery 实现分布式调度。

---

## API 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/research-projects` | 项目列表（支持 `?status=REPORT_READY` 过滤） |
| `POST` | `/research-projects` | 创建研究项目 |
| `GET` | `/research-projects/{id}/outline` | 获取大纲 |
| `PUT` | `/research-projects/{id}/outline` | 确认/修改大纲 |
| `POST` | `/research-projects/{id}/report-tasks` | 提交报告生成任务 |
| `POST` | `/research-projects/{id}/report-render-tasks` | 提交独立报告渲染任务 |
| `GET` | `/research-projects/{id}/reports/latest` | 获取最新报告 |
| `GET` | `/research-projects/{id}/reports/export?format=md\|html` | 导出报告为文件 |
| `GET` | `/tasks/{task_id}` | 查询后台任务状态 |

---

## 配置要点

`.env` 文件关键项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_PROVIDER` | 模型提供商：`openai` / `deepseek` | `deepseek` |
| `LLM_MODEL_NAME` | 模型名 | `deepseek-v4-pro` |
| `MONGODB_URI` | 连接串（需带用户名密码和 authSource） | — |
| `TAVILY_API_KEY` | Tavily 搜索 API Key（未配置时搜索跳过不报错） | — |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | — |
| `ENABLE_RAGFLOW` | 是否启用内部知识库检索 | `false` |

**注意事项：**
- pydantic-settings 读取 `.env` 后不会自动注入 `os.environ`，LangChain 的 `ChatDeepSeek` 直接从环境变量读 key。`config.py` 中 `_export_to_env()` 负责桥接
- Windows 启动必须设置 `PYTHONIOENCODING=utf-8`，否则 LangGraph 内部 asyncio 任务处理中文时抛 `UnicodeEncodeError`（`main.py` 开头已重配 stdout/stderr 为 UTF-8 双重保险）

---

## 工具函数

| 工具 | 文件 | 功能 |
|------|------|------|
| `external_search` | `tools/external_search.py` | Tavily 公开互联网搜索 |
| `read_web_page` | `tools/web_reader.py` | 读取网页并提取纯文本 |
| `ragflow_search` | `tools/ragflow_search.py` | RAGFlow 内部知识库检索 |
| `save_research_section` | `tools/research_workspace.py` | 逐章节落库（含约 20 条校验规则） |
| `write_html_report` | `tools/report_writer.py` | 确定性 HTML 报告渲染 |
| `render_report_markdown` | `tools/report_writer.py` | 确定性 Markdown 报告渲染 |

---

## 前端功能

`static/index.html` 为单页应用，两个 Tab：

- **新建研究**：五步向导（创建项目 → 生成大纲 → 确认大纲 → 生成报告 → 查看报告），含导出 MD/HTML 按钮
- **历史报告**：展示已完成的项目列表，支持在线查看和 MD 导出

---

## 扩展方向

### 业务扩展

| 方向 | 说明 |
|------|------|
| 问数智能体 | 面向企业内部结构化数据库，负责 SQL 生成和指标查询 |
| 竞品分析智能体 | 跟踪公司、产品、融资、价格、渠道信息 |
| PDF 导出 | 基于 weasyprint 将 HTML 转 PDF |
| Celery 任务队列 | 替换 asyncio.create_task，支持分布式多实例调度 |
| 登录和权限 | 多用户场景下的项目隔离和权限控制 |

### Agent 工程演进

| 方向 | 说明 | 对应 Agent 学习清单 |
|------|------|---------------------|
| **MCP 工具协议** | 将现有工具封装为 MCP Server，实现工具的标准化和跨平台复用 | 第2周、⭐⭐⭐⭐⭐ |
| **流式输出（SSE）** | 实时展示 Agent 思考过程和工具调用，替代当前"黑盒等待"体验 | 第3周 |
| **Agent 可观测性** | 接入 LangSmith/LangFuse 追踪每一步推理和工具调用链路 | 第3周 |
| **Agent 评估体系** | 任务完成率、步骤效率、工具调用准确率等定量指标 | 第3周 |
| **长期向量记忆** | 历史研究报告存入向量库，支持跨项目知识复用和趋势分析 | 第2周 |
| **浏览器级网页抓取** | 用 Playwright 替代当前轻量 HTML 解析，支持 JS 渲染页面和视觉理解 | 实战项目 |
| **代码执行沙箱** | E2B/Docker 沙箱执行数据分析和图表生成代码 | 第3周 |

---

## Agent 智能体开发技术覆盖

对照 Agent 学习清单（第1周基础 → 第2周进阶 → 第3周生产），本项目已覆盖和尚未覆盖的技术：

| 类别 | 已使用 | 未使用 |
|------|--------|--------|
| **框架** | LangGraph（图编排 + Checkpointer）、DeepAgents（子智能体 + 虚拟文件系统） | CrewAI、AutoGen、MCP 协议、OpenAI Assistants API |
| **工具调用** | OpenAI Function Calling、工具函数模式 | MCP Server/Client、Anthropic Tool Use |
| **工具集成** | Tavily 搜索、RAGFlow 内部检索、网页轻量解析 | 代码沙箱、浏览器渲染（Playwright）、SQL/Text-to-SQL、邮件/日历 |
| **记忆系统** | LangGraph State（短期）+ MemorySaver 持久化 | 向量长期记忆、ConversationSummaryMemory |
| **多 Agent** | Supervisor/Handoff 模式（Manager → Search） | CrewAI 角色分工、AutoGen 多 Agent 对话 |
| **生产特性** | 工具校验闭环（~20 条规则 + ok=false 重试）、逐章节补写（最多 4 轮） | 流式输出、可观测性（LangSmith）、评估体系、安全沙箱 |

本项目扎实覆盖了 Agent 学习清单**第1周+第2周的大部分内容**，是一个**合格的进阶级 Multi-Agent 项目**。第3周的生产化能力（MCP、流式、可观测性、评估、沙箱）可作为后续进阶方向。

---

## 学习资源

项目附带完整学习笔记（`学习笔记/` 目录），按代码执行链路组织，共 11 篇：

| 序号 | 模块 | 核心内容 |
|------|------|----------|
| 01 | 项目骨架与配置 | 依赖全景、配置管理、应用工厂、数据流向 |
| 02 | 数据模型层 | 7 个状态枚举、递归大纲结构、请求/响应模型 |
| 03 | 数据访问层 | MongoDB 异步单例、逐章节 UPSERT、对象存储抽象 |
| 04 | API 路由层 | 8 个端点、状态门禁模式、异步任务 API |
| 05 | 后台任务调度 | 4 种任务流程、asyncio.create_task、二段式报告生成 |
| 06 | Agent 核心门面 | 门面模式、内部数据类型、逐章节循环落库、输出解析 |
| 07 | Agent 系统 Prompt | 职责边界、任务分支、Few-shot 工程、虚拟文件系统 |
| 08 | Agent 工具层 | Tavily 搜索、网页读取、章节校验（~20 条规则） |
| 09 | 报告渲染引擎 | 三段式渲染、目录树算法、概览/叶子章节、纯 CSS 折叠 |
| 10 | 基础设施与前端 | SPA 交互流程、Docker 部署、部署架构 |
| 11 | 课件补充 | chatbot vs 深度研究对比、状态机、LLM 边界原则、常见陷阱 |

建议阅读顺序：[00_学习方法](学习笔记/00_学习方法.md)
