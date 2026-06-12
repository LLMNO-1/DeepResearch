![image-20260611135331466](C:\Users\faker\AppData\Roaming\Typora\typora-user-images\image-20260611135331466.png)

#### 必学框架

| 框架/工具                 | 用途                           | 优先级 | 说明          |
| ------------------------- | ------------------------------ | ------ | ------------- |
| **LangChain Agent**       | Agent 基础框架                 | ⭐⭐⭐⭐   | 入门用        |
| **LangGraph ★核心**       | 图编排框架，支持循环/分支/状态 | ⭐⭐⭐⭐⭐  | 生产首选      |
| **CrewAI**                | Multi-Agent 协作框架           | ⭐⭐⭐⭐   | 多 Agent 场景 |
| **OpenAI Assistants API** | 官方 Agent API                 | ⭐⭐⭐⭐   | 快速原型      |
| **AutoGen (微软)**        | Multi-Agent 对话框架           | ⭐⭐⭐    | 多 Agent 研究 |
| **MCP (Anthropic)**       | 模型上下文协议，标准化工具连接 | ⭐⭐⭐⭐⭐  | 新标准，必学  |
| **Toolhouse**             | 工具市场，开箱即用的工具       | ⭐⭐⭐    | 快速集成      |

#### 必学组件------工具调用

| 技术                        | 说明                                | 优先级 |
| --------------------------- | ----------------------------------- | ------ |
| **OpenAI Function Calling** | 工具调用的事实标准                  | ⭐⭐⭐⭐⭐  |
| **Anthropic Tool Use**      | Claude 的工具调用                   | ⭐⭐⭐⭐   |
| **LangChain @tool**         | 工具定义装饰器                      | ⭐⭐⭐⭐⭐  |
| **MCP Server/Client**       | 标准化工具协议                      | ⭐⭐⭐⭐⭐  |
| **MCP 工具生态**            | 文件系统、数据库、搜索等 MCP Server | ⭐⭐⭐⭐   |

#### 必学组件------工具集成

| 工具类型      | 具体技术                                         | 优先级 |
| ------------- | ------------------------------------------------ | ------ |
| **搜索**      | Tavily API、Bing Search API、SerpAPI、DuckDuckGo | ⭐⭐⭐⭐⭐  |
| **代码执行**  | E2B、Jupyter Kernel、Docker 沙箱                 | ⭐⭐⭐⭐   |
| **数据库**    | SQLDatabase (LangChain)、Text-to-SQL             | ⭐⭐⭐⭐   |
| **文件系统**  | LangChain DirectoryLoader、MCP Filesystem        | ⭐⭐⭐⭐   |
| **API 调用**  | requests/httpx、LangChain RequestsTool           | ⭐⭐⭐⭐   |
| **网页浏览**  | Playwright、Selenium、Browser Use                | ⭐⭐⭐⭐   |
| **邮件/日历** | Gmail API、Google Calendar API                   | ⭐⭐⭐    |

#### 必学组件------记忆系统

| 技术                          | 说明                   | 优先级 |
| ----------------------------- | ---------------------- | ------ |
| **ConversationBufferMemory**  | 完整对话历史           | ⭐⭐⭐⭐   |
| **ConversationSummaryMemory** | 对话摘要压缩           | ⭐⭐⭐⭐   |
| **向量记忆**                  | 历史对话存入向量库检索 | ⭐⭐⭐⭐   |
| **LangGraph State**           | 图状态管理             | ⭐⭐⭐⭐⭐  |

#### 必学组件------多 Agent

| 框架          | 模式                       | 优先级 |
| ------------- | -------------------------- | ------ |
| **LangGraph** | 图编排，Supervisor/Handoff | ⭐⭐⭐⭐⭐  |
| **CrewAI**    | 角色分工，顺序/并行/层级   | ⭐⭐⭐⭐   |
| **AutoGen**   | 多 Agent 对话              | ⭐⭐⭐    |

#### Agent 学习清单

**第1周：基础 Agent**

-  OpenAI Function Calling：定义工具、解析调用、返回结果
-  LangChain Tool：@tool 装饰器、StructuredTool
-  LangChain Agent：create_react_agent、AgentExecutor
-  ReAct 模式：Thought → Action → Observation 循环
-  常用工具集成：搜索（Tavily）、计算器、文件操作
-  构建第一个 Agent：能搜索 + 能计算 + 能回答

**第2周：进阶 Agent**

-  LangGraph 核心概念：State、Node、Edge、Conditional Edge
-  LangGraph 构建 ReAct Agent（手动实现，不用内置）
-  LangGraph 的 Human-in-the-Loop：interrupt_before / interrupt_after
-  LangGraph 的持久化：Checkpointer、线程管理
-  Multi-Agent：Supervisor 模式、Handoff 模式
-  记忆系统：短期（State）+ 长期（向量库）
-  MCP 协议：理解 Server/Client 架构，使用现有 MCP Server

**第3周：生产 Agent**

-  Agent 安全：工具权限控制、沙箱执行
-  Agent 评估：任务完成率、步骤效率、工具调用准确率
-  错误处理：重试、降级、超时、最大步骤限制
-  流式输出：Agent 思考过程的流式展示
-  Agent 可观测性：LangSmith 追踪每一步
-  部署：FastAPI 封装 Agent API

#### Agent 实战项目

| 项目             | 技术栈                                 | 说明     |
| ---------------- | -------------------------------------- | -------- |
| 搜索问答 Agent   | LangChain + Tavily + OpenAI            | 入门级   |
| 数据分析 Agent   | LangGraph + SQL + Matplotlib           | 进阶级   |
| 研究助手 Agent   | CrewAI + 搜索 + RAG                    | 多 Agent |
| 自动化运维 Agent | LangGraph + Shell + 监控 API           | 生产级   |
| 浏览器 Agent     | Playwright + Vision + Function Calling | 前沿级   |