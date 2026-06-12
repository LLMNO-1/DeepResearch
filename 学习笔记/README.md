# DeepResearch 学习笔记

按代码执行链路（请求入口 → 路由 → 后台任务 → Agent → 工具 → 渲染 → 响应）组织，共 10 个模块。

## 模块列表

| 序号 | 模块 | 文件 | 核心内容 |
|------|------|------|----------|
| 1 | 项目骨架与配置 | [01_项目骨架与配置.md](01_项目骨架与配置.md) | pyproject.toml 依赖全景、pydantic-settings 配置管理、FastAPI 应用工厂、请求数据流向总览 |
| 2 | 数据模型层 | [02_数据模型层_Schemas.md](02_数据模型层_Schemas.md) | 枚举体系（7个状态）、请求/响应 Pydantic 模型、OutlineNode 递归结构、跨字段校验 |
| 3 | 数据访问层 | [03_数据访问层_Repository.md](03_数据访问层_Repository.md) | MongoDB 异步客户端单例、逐章节 UPSERT 设计、对象存储抽象、版本号自增策略 |
| 4 | API 路由层 | [04_API路由层_Routers.md](04_API路由层_Routers.md) | 6个端点完整覆盖生命周期、状态门禁模式、异步任务 API 设计 |
| 5 | 后台任务调度 | [05_后台任务调度_Background.md](05_后台任务调度_Background.md) | 4种任务执行流程、asyncio.create_task 方案、二段式报告生成、统一错误处理 |
| 6 | Agent 核心 | [06_Agent核心_ResearchAgent门面.md](06_Agent核心_ResearchAgent门面.md) | ResearchAgent 门面模式、内部数据类型体系、逐章节循环落库、确定性聚合、输出解析 |
| 7 | Agent 系统 Prompt | [07_Agent系统Prompt.md](07_Agent系统Prompt.md) | 职责边界设计、任务类型分支、事实置信度框架、Few-shot 工程、虚拟文件系统策略 |
| 8 | Agent 工具层 | [08_Agent工具层_Tools.md](08_Agent工具层_Tools.md) | Tavily 搜索、网页读取、RAGFlow 检索、章节落库校验（约20条规则） |
| 9 | 报告渲染引擎 | [09_报告渲染引擎.md](09_报告渲染引擎.md) | 三段式确定性渲染、目录树构建算法、概览/叶子章节区分、纯 CSS 折叠目录 |
| 10 | 基础设施与前端 | [10_基础设施与前端.md](10_基础设施与前端.md) | 前端 SPA 交互流程、MongoDB/RAGFlow Docker 部署、部署架构图、局限性 |
| 11 | 课件补充内容 | [11_课件补充内容.md](11_课件补充内容.md) | chatbot vs 深度研究对比、状态机设计、asyncio.create_task 详解、MongoDB 入门、LLM 边界原则、多智能体难点、端到端验证、问题排查 |

## 建议阅读顺序

**如果关注业务逻辑**：1 → 4 → 5 → 6 → 剩余

**如果关注 AI Agent 设计**：6 → 7 → 8 → 9

**如果关注工程架构**：1 → 2 → 3 → 4 → 5

**如果想快速跑起来**：先看 1 和 10 的部署配置，再看 4 的 API 端点
