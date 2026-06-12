# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

企业内部 AI 研究报告工作台后端服务。用户提交研究主题 → 系统通过 DeepAgents 多智能体协作完成资料检索、事实整理、章节撰写 → 最终渲染为结构化 HTML 研究报告。

## 技术栈

- **Python 3.12+**，包管理器 **uv**（`pyproject.toml` + `uv.lock`）
- **FastAPI** 提供 REST API，**MongoDB**（异步 pymongo）持久化
- **DeepAgents**（基于 LangGraph）驱动多智能体研究流程
- **Tavily API** 公开搜索，**RAGFlow** 内部知识库检索
- 前端为 `static/index.html` 单页面，通过 FastAPI StaticFiles 挂载

## 常用命令

```bash
# 安装依赖
uv sync

# 安装含开发依赖
uv sync --group dev

# 启动开发服务器（Windows 必须前置 PYTHONIOENCODING）
# PowerShell:
#   $env:PYTHONIOENCODING='utf-8'; uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# CMD:
#   set PYTHONIOENCODING=utf-8 && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 代码检查
uv run ruff check .

# 运行测试
uv run pytest

# MongoDB Docker 部署（有鉴权，连接串需带用户名密码和 authSource）
docker build -f mongo/Dockerfile.mongodb -t deep-research-mongo:7.0 mongo
docker run -d --name deep-research-mongo -p 27017:27017 -v deep-research-mongo-data:/data/db deep-research-mongo:7.0
# 默认应用用户: deepresearch / deepresearch_dev，数据库: deep_research
# MONGODB_URI=mongodb://deepresearch:deepresearch_dev@<host>:27017/deep_research?authSource=deep_research

# RAGFlow 及其依赖服务
docker compose -f ragflow-docker/docker-compose.yml up -d
```

## 架构分层

```
app/
├── main.py              # FastAPI 应用工厂，挂载路由和静态文件
├── config/config.py     # pydantic-settings 配置，从 .env 读取
├── routers/             # API 路由层——参数校验、状态检查，委托 background 执行
├── background/          # 后台异步任务——编排 Agent 调用和 repository 持久化
├── agents/              # DeepAgents 智能体门面 + 系统 Prompt（Markdown 文件）
├── tools/               # Agent 可调用的工具函数（搜索、网页读取、章节保存、报告渲染）
├── repository/          # MongoDB 数据访问层（项目、任务、报告版本、对象存储）
└── schemas/             # Pydantic 请求/响应模型和枚举定义
```

## 核心流程

### 研究项目生命周期

1. **创建项目** `POST /api/v1/research-projects` → 状态 `BRIEF_GENERATING`，后台异步生成研究任务书和大纲
2. **大纲确认** 用户通过 `PUT /outline` 确认（`CONFIRM`）或修改（`REVISE`）大纲
3. **报告生成** `POST /report-tasks` → 状态 `RESEARCH_RUNNING`，后台执行研究和 HTML 渲染
4. **报告渲染** `POST /report-render-tasks` → 基于已落库 research_result 重新渲染 HTML（不重新研究）
5. **获取报告** `GET /reports/latest` → 返回最新 HTML 报告

前端通过轮询 `GET /tasks/{task_id}` 跟踪后台任务进度。

### 智能体协作模型

- **ResearchManagerAgent**（`research_manager.md`）：负责大纲设计、拆解检索问题、委托检索子智能体、整理事实/洞察、撰写章节正文，通过 `save_research_section` 逐章节落库。**不直接搜索、不生成 HTML**
- **SearchAgent**（`search_agent.md`）：负责公开搜索（Tavily）、网页读取、RAGFlow 内部检索、事实提取和冲突识别。**不写报告正文**

### 研究与渲染分离

- **研究阶段**：manager_agent 协调 search_agent 检索 → 撰写章节正文+证据链 → 通过 `save_research_section` 逐章节保存到 MongoDB
- **渲染阶段**：`report_writer.py` 纯确定性 Python 代码，读取已落库 research_result，生成 HTML（Markdown 渲染、目录树、证据引用、表格、来源列表）。**不允许新增事实或调用 LLM**

### 后台任务机制

`background/research_tasks.py` 中所有后台任务通过 `asyncio.create_task` 提交到当前事件循环，不依赖 Celery/Redis 队列。任务失败时记录日志并标记为 `FAILED`。

## 关键依赖说明

- `deepagents`：DeepAgents 框架，提供 `create_deep_agent` 和虚拟文件系统
- `langgraph-checkpoint`：MemorySaver 用于 agent 对话状态持久化
- `modelscope` / `huggingface-hub`：用于 RAGFlow 的 embedding 模型
- `markdown-it-py`：报告渲染中将章节 body Markdown 转为 HTML

## 配置要点

- `.env` 文件（从 `.env.example` 复制）控制所有运行参数
- `LLM_PROVIDER` 支持 `openai` 和 `deepseek`，模型名格式为 `provider:model_name`
- Tavily API Key 未配置时搜索工具返回 `skipped` 而非报错
- RAGFlow 通过 `ENABLE_RAGFLOW` 开关控制是否作为 search_agent 工具
- 报告 HTML 存储后端支持 `local`（本地文件）和 `minio`（S3，尚未实现）
- **pydantic-settings 不会自动把 `.env` 值注入 `os.environ`**——`config.py` 中 `_export_to_env()` 负责将 LLM 密钥同步到 `os.environ`，供 LangChain 等直接读环境变量的库使用
- **Windows 启动必须设置 `PYTHONIOENCODING=utf-8`**，否则 LangGraph 内部 asyncio 任务处理中文时会报 `UnicodeEncodeError`（`main.py` 开头已重配 stdout/stderr 为 UTF-8 作为双重保险）

### 报告导出

支持将已生成的报告导出为 Markdown 或 HTML 文件下载：

- `GET /reports/export?format=md` — 导出 Markdown
- `GET /reports/export?format=html` — 导出 HTML
- 基于已落库 `research_result` 实时渲染，不额外存储
- `report_writer.py` 中 `render_report_markdown()` 从 Document IR 生成 MD，`render_report_html()` 生成 HTML，两者共用 `build_report_document()` 中间表示

### 前端功能

`static/index.html` 为单页应用，两个 tab：

- **新建研究**：五步向导——创建项目 → 生成大纲 → 确认大纲 → 生成报告 → 查看报告
- **历史报告**：展示 `REPORT_READY` 状态的项目列表，支持在线查看和导出 MD
- 路由端点 `GET /research-projects` 支持 `?status=` 过滤，供历史页调用
- `research_project_repository.list_projects()` 提供按状态过滤和分页查询
