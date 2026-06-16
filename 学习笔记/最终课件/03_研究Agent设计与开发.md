# DeepAgents框架和本项目的Agent设计与开发

## 1.DeepAgents概述

### 1.1 和langchain、langgraph的区别

 Deep Agents 构建在 LangChain 的 Agent 基础组件之上，并使用 LangGraph runtime 提供持久执行、流式输出、人类介入等能力。

相比直接使用 `create_agent`，DeepAgents 默认集成了任务规划、虚拟文件系统、上下文压缩、子智能体、长期记忆、权限和人类审批等能力，更适合复杂、多步骤、长上下文任务。

三者关系可以这样理解：

| 层级          | 组件                       | 主要解决的问题                              | 适合场景                   |
| ----------- | ------------------------ | ------------------------------------ | ---------------------- |
| 应用级 harness | DeepAgents               | 开箱即用地构建复杂 Agent，内置规划、文件系统、子智能体和上下文管理 | 研究、编码、长任务、多步骤自动化       |
| Agent 框架    | LangChain `create_agent` | 构建标准模型-工具循环，并通过 middleware 扩展        | 轻量工具调用 Agent、需要自定义少量能力 |
| 编排运行时       | LangGraph                | 自定义有状态工作流、节点、边、持久化和中断恢复              | 复杂状态机、强控制流、多 Agent 图编排 |

对本项目来说，选择 DeepAgents 的原因不是“不能用 LangChain 或 LangGraph”，而是研究报告生成天然包含长任务规划、检索材料沉淀、子任务委托和上下文压缩。

### 1.2 核心原理

下面的章节，会详细的介绍，deepagents当中3个方面的详细原理。面试的时候，可以直接去跟面试官说，

#### 1.2.1 中间件机制

通过create_agent创建的简单agent循环，代码如下：

```python
from langchain.agents import create_agent

agent = create_agent(
    model="deepseek-chat",
    tools=[...],
)
```

机制如下所示：

<img src="file:///C:/Users/m1881/AppData/Roaming/marktext/images/2026-06-12-16-59-04-image.png" title="" alt="" data-align="center">

只有简单的model调用和tools之间的流转。

而在此基础上，create_agent还为我们提供了middleware参数，从而可以通过middleware，来加强这个循环过程，如下所示：

<img src="file:///C:/Users/m1881/AppData/Roaming/marktext/images/2026-06-12-17-00-57-image.png" title="" alt="" data-align="center">

整个调用Agent的过程，可以在不改变model和tools的相关代码前提下，实现多处调整：

- before/after_agent：在agent调用的起始输入和终点输出，进行相关处理（切片编程思想）；
- before/after_model: 在model调用的前后，进行相关处理（切片编程思想）；
- wrap_tool/model_call: 通过handler回调的方式，拦截工具/模型执行，可以为工具执行/模型执行，添加重试，缓存，多次调用等相关逻辑（代理思想）。

另外，middleware还能够为模型额外添加其可调用的工具。

AgentMiddleware基类如下所示：

```python
class AgentMiddleware(Generic[StateT, ContextT, ResponseT]):
    """Base middleware class for an agent.

    Subclass this and implement any of the defined methods to customize agent behavior
    between steps in the main agent loop.

    Type Parameters:
        StateT: The type of the agent state. Defaults to `AgentState[Any]`.
        ContextT: The type of the runtime context. Defaults to `None`.
        ResponseT: The type of the structured response. Defaults to `Any`.
    """
    tools: Sequence[BaseTool]
    """Additional tools registered by the middleware."""

    @property
    def name(self) -> str:
        """The name of the middleware instance.

        Defaults to the class name, but can be overridden for custom naming.
        """
        return self.__class__.__name__

    def before_agent(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        pass

    async def abefore_agent(
        self, state: StateT, runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        pass

    def before_model(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        pass

    async def abefore_model(
        self, state: StateT, runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        pass

    def after_model(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        pass

    async def aafter_model(
        self, state: StateT, runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        pass

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT] | AIMessage | ExtendedModelResponse[ResponseT]:
        pass

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT] | AIMessage | ExtendedModelResponse[ResponseT]:
        pass

    def after_agent(self, state: StateT, runtime: Runtime[ContextT]) -> dict[str, Any] | None:
        pass

    async def aafter_agent(
        self, state: StateT, runtime: Runtime[ContextT]
    ) -> dict[str, Any] | None:
        pass

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        pass


    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        pass
```

##### 1.2.1.1 ToDoListMiddleware

ToDoListMiddleware会在`wrap_model_call`处生效，在每次调用大模型时，都会为大模型添加一个system prompt，并为大模型添加一个`write_todos`的工具。

实现如下：

```python
class TodoListMiddleware(AgentMiddleware[PlanningState[ResponseT], ContextT, ResponseT]):
        def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        """Update the system message to include the todo system prompt.

        Args:
            request: Model request to execute (includes state and runtime).
            handler: Async callback that executes the model request and returns
                `ModelResponse`.

        Returns:
            The model call result.
        """
        if request.system_message is not None:
            new_system_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_system_content = [{"type": "text", "text": self.system_prompt}]
        new_system_message = SystemMessage(
            content=cast("list[str | dict[str, str]]", new_system_content)
        )
        return handler(request.override(system_message=new_system_message))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        """Update the system message to include the todo system prompt.

        Args:
            request: Model request to execute (includes state and runtime).
            handler: Async callback that executes the model request and returns
                `ModelResponse`.

        Returns:
            The model call result.
        """
        if request.system_message is not None:
            new_system_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_system_content = [{"type": "text", "text": self.system_prompt}]
        new_system_message = SystemMessage(
            content=cast("list[str | dict[str, str]]", new_system_content)
        )
        return await handler(request.override(system_message=new_system_message))
```

##### 1.2.1.2 FileSystemMiddleware

`FileSystemMiddleware`实现了wrap_model_call和wrap_tool_call。

在wrap_model_call当中，FileSystemMiddleware会在原SystemPrompt的基础上面，添加上让模型使用文件系统相关工具：

```textile
## Following Conventions

- Read files before editing — understand existing content before making changes
- Mimic existing style, naming conventions, and patterns

## Filesystem Tools `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`

You have access to a filesystem which you can interact with using these tools.
All file paths must start with a /. Follow the tool docs for the available tools, and use pagination (offset/limit) when reading large files.

- ls: list files in a directory (requires absolute path)
- read_file: read a file from the filesystem
- write_file: write to a file in the filesystem
- edit_file: edit a file in the filesystem
- glob: find files matching a pattern (e.g., "**/*.py")
- grep: search for text within files

## Large Tool Results

When a tool result is too large, it may be offloaded into the filesystem instead of being returned inline. In those cases, use `read_file` to inspect the saved result in chunks, or use `grep` within `{large_tool_results_prefix}/` if you need to search across offloaded tool results and do not know the exact file path. Offloaded tool results are stored under `{large_tool_results_prefix}/<tool_call_id>`.
```

另外，还会将过长的消息进行裁剪，然后再来进行模型调用。

同样，在wrap_tool_call中，该middleware也会对工具产生的消息进行裁剪，裁剪后的消息会放到文件系统中，并告知模型，可以通过读取文件的方式，来获取到消息内容。

##### 1.2.1.3 SubAgentMiddleware

`SubAgentMiddleware`实现了wrap_model_call，在每次调用模型前，会为模型添加上新的system_prompt： 告诉模型当前有哪些子agent可以使用，每个子agent的描述信息。

SubAgentMiddleware中，为主智能体提供子智能体调用的方式，是给主智能体新添加了一个task的tool，这个tool的参数包含了：调用的子智能体的名称，和调用信息。

```python
class SubAgentMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
        def __init__(
        self,
        *,
        backend: BackendProtocol | BackendFactory,
        subagents: Sequence[SubAgent | CompiledSubAgent],
        system_prompt: str | None = TASK_SYSTEM_PROMPT,
        task_description: str | None = None,
        state_schema: type | None = None,
    ) -> None:
        """Initialize the `SubAgentMiddleware`."""
        super().__init__()

        if not subagents:
            msg = "At least one subagent must be specified"
            raise ValueError(msg)
        self._backend = backend
        self._subagents = subagents
        self._state_schema = state_schema
        subagent_specs = self._get_subagents()
        self.subagent_names: frozenset[str] = frozenset(spec["name"] for spec in subagent_specs)
        """Declared subagent names. Public so streamers can discover them
        without introspecting the `task` tool's closure."""

        task_tool = _build_task_tool(subagent_specs, task_description)

        # Build system prompt with available agents
        if system_prompt and subagent_specs:
            agents_desc = "\n".join(f"- {s['name']}: {s['description']}" for s in subagent_specs)
            self.system_prompt = system_prompt + "\n\nAvailable subagent types:\n\n" + agents_desc
        else:
            self.system_prompt = system_prompt

        self.tools = [task_tool]
```

##### 1.2.1.4 其他Middleware

create_deep_agent还添加了如下middleware，仅做了解:

- SummarizationMiddleware

- PatchToolCallsMiddleware

- 其他。。。

- ### 维度一：一句话说明核心定位（架构高观点）

  > “这个中间件的核心作用是**将单智能体（Single Agent）低成本地升级为多智能体集群（Multi-Agent System）**。它采用**主从架构（Supervisor-Worker Pattern）**，通过生命周期钩子（Hooks）把‘团队协作与任务分发’的复杂逻辑从主模型中剥离出来，实现了智能体之间的高内聚和低耦合。”

  ### 维度二：深入剖析它解决了什么工程痛点？（体现落地经验）

  在实际大模型应用开发中，它主要解决了单 Agent 的三大瓶颈：

  - **解决“上下文爆炸（Context Bloat）”与 Token 成本痛点**：

    - *普通做法*：把搜索、数据库、代码执行等所有工具和提示词都塞给一个 Agent，Prompt 变得巨长，不仅极耗 Token，而且大模型容易迷失（Lost in the Middle）。
    - *中间件做法*：通过这个中间件，主 Agent 只需要知道子 Agent 的简短描述即可。具体的专业工具和长 Prompt 都被隔离在子 Agent 内部，**只有当任务派发给特定子 Agent 时才加载对应的上下文**，极大地节省了成本。

  - **解决大模型的“幻觉与注意力分散”问题**：

    - 大模型工具一多，调用时就容易出错。这个中间件把复杂的工具集按业务拆分给了专门的子 Agent（比如让 SearchAgent 专门负责搜素，DBAgent 专门负责写库）。主 Agent 只需要专心做好“需求分析和任务指派（Router）”这一件事，各个子 Agent 各司其职，**大幅提升了工具调用的准确率**。

  - **解决系统扩展性差（Scalability）的问题**：

    - 如果不采用中间件，新增一个 AI 功能就要去改动核心的 Prompt 和主循环。而使用这个中间件，代码里通过 `task_tool = _build_task_tool(subagent_specs)` **动态将子 Agent 转化为主 Agent 的工具**。这意味着团队未来想增加一个“财务报表分析 Agent”，只需要作为参数传入 `subagents` 列表即可，**核心架构一行都不用改，完美契合开闭原则（OCP）**

    - “总的来说，这个类的本质是一个**基于工具化（Toolification）的高级多智能体路由器**。

      它的工作流可以完美概括为：**‘统一接管 $\rightarrow$ 动态包装 $\rightarrow$ 模型决策 $\rightarrow$ 拦截路由’**。

      任务进来时，它作为中间件首先拦截并初始化环境；它将具体的子 Agent 集群动态编译为一个通用的 `task_tool` 暴露给主模型；主模型基于语义理解进行‘点将’，输出目标子 Agent 的名字；最后，中间件精准拦截这个工具调用，将控制权和上下文平滑切换到对应的子 Agent 内部执行。

      这种设计让单模型轻松具备了指派、调度和协同复杂团队的能力。”

    

#### 1.2.2 文件系统后端

FileSystemMiddleware依赖文件系统后端来进行文件读写。文件系统后端是实现以下接口的类：

| 方法                                | 功能        |
| --------------------------------- | --------- |
| ls                                | 列出目录内容    |
| read                              | 分页读取文件    |
| write                             | 创建新文件     |
| edit                              | 精确字符串匹配   |
| grep                              | 文本搜索      |
| glob                              | 通配符匹配文件   |
| upload_files() / download_files() | 批量上传/下载文件 |

在传统的 Web 开发中，服务器读写文件很简单。但在 Agent 领域，大模型调用 `write`（写文件）或 `grep`（搜索）时，必须有一个安全、受控、可随时迁移的底层支撑，这就引出了图中提到的四种**后端实现（Backends）**。

有如下的文件系统后端：

- **`StateBackend`（默认实现：内存型沙盒）**

  - *怎么理解*：文件根本没有写到真正的硬盘上，而是变成了 LangGraph 运行状态（Agent State）里的一段字符串数据。
  - *优缺点*：速度极快，对话结束文件就消失。**线程间不共享**，这意味着用户 A 在聊天时让 Agent 临时存个小表格，用户 B 绝对看不见，天然做到安全隔离。

  **`FileSystemBackend`（本地物理磁盘）**

  - *怎么理解*：Agent 执行 `write` 时，真的在你服务器的 `/home/user/data` 目录下创建了一个 `.txt` 文件。
  - *关键设计*：图里提到了“**设置虚拟化路径，将路径约束在指定目录下**”。这在安全上叫 **Chroot / 目录沙盒**。绝对不能让 Agent 有权限去读写你服务器的根目录（比如 `/etc/passwd`），必须把它死死锁在指定的文件夹里。

- StoreBackend：文件存在Langgraph的Base Store当中，生产环境替代本地磁盘。容器重启、横向扩容、本地磁盘不可依赖时，用 StoreBackend 接 Mongo/Postgres/Redis/云存储背后的 LangGraph store。

- **`CompositeBackend`（路由网关模式）**

  - *怎么理解*：它是个分发器。比如路径是 `/temp/log.txt` 就走内存 `StateBackend`；路径是 `/report/2026.pdf` 就走云存储 `StoreBackend`。

注意：子智能体的FileSystemMiddleware使用的是主agent同一个backend实例。这意味着子智能体操作的文件和主智能体是同一个文件空间。

- **优雅的做法（本图的设计）**：因为主从 Agent 共享同一个“物理办公室（Backend 实例）”。主 Agent 把文件存到办公室的群组盘里，然后对子 Agent 说：“*我把表格放共享网盘里了，名字叫 `data.xlsx`，你用 `read` 方法去读一下。*”

子 Agent 听懂了，直接去同一个空间里读取、修改，改完再告诉主 Agent。**整个过程只传递了一个简短的文件路径字符串，没有浪费任何多余的 Token！**避免了大文件在多智能体协同协作时导致的内容爆 Token 问题，实现了高效的‘指针式（Pointer-based）’上下文传递。



下面以使用skills作为例子，来讲解StateBackend、FileSystemBackend和StoreBackend的不同：

使用StateBackend:

```python
  from deepagents import create_deep_agent
  #专门用来把一段 Python 字符串（文本内容）打包转换成带时间戳、符合框架标准的标准虚拟文件数据对象。
  from deepagents.backends.utils import create_file_data

  agent = create_deep_agent(
      model="openai:gpt-4.1-mini",
      tools=[],
      #接着，SkillsMiddleware（技能中间件）被触发，由于它的监听路径就是 /skills/project/，它一扫描，发现里面躺着一本 SKILL.md！它会立刻读取它，提取其元数据，并将这份“调研指导 SOP”动态拼接进主 Agent 的上下文（System Prompt）中。
      skills=["/skills/project/"],
  )

  skill_md = """---
  name: web-research
  description: 用于公开资料检索、来源整理和结论归纳
  ---

  # Web Research

  当用户要求调研某个主题时：
  1. 明确问题范围
  2. 收集来源
  3. 整理关键事实
  4. 输出带来源的总结
  """

  result = agent.invoke(
      {
          "messages": [
              {"role": "user", "content": "帮我调研一下新能源汽车行业趋势"}
          ],
          #运行原理：因为你刚刚在 configurable.thread_id 里指定了会话 ID，框架启动后，中间件（还记得上一题图中的 before_agent 吗？）会立刻把 files 里的数据写入当前会话的存储空间。  
          "files": {
              "/skills/project/web-research/SKILL.md": create_file_data(skill_md),
          },
      },
      config={
          "configurable": {
              "thread_id": "demo-statebackend-skills"
          }
      },
  )
```

**注意**：使用StateBackend的时候，必须在invoke时，传入一个files key，作为skills的具体内容信息，仅在构建create_deep_agents时，传入skills目录，agent在运行过程中，无法读取到具体的skills内容。

使用FileSystemBackend:

```python
  from deepagents import create_deep_agent
  from deepagents.backends import FilesystemBackend

  backend = FilesystemBackend(
      root_dir="/home/m1881/pycharm_projects/DeepResearch"
  )

  agent = create_deep_agent(
      model="openai:gpt-4.1-mini",
      tools=[],
      backend=backend,
      skills=["/agent_skills/"],
  )

  result = agent.invoke(
      {
          "messages": [
              {"role": "user", "content": "帮我调研一下新能源汽车行业趋势"}
          ],
      },
      config={
          "configurable": {
              "thread_id": "demo-filesystembackend-skills"
          }
      },
  )
```

**注意**：使用FileSystemBackend的时候，在invoke，无需传入files，因为FilesystemBackend可以真实读取磁盘上面文件内容。

使用StoreBackend:

```python
  from deepagents import create_deep_agent
  from deepagents.backends import StoreBackend
  from langgraph.store.memory import InMemoryStore

  store = InMemoryStore()

  skill_md = """---
  name: web-research
  description: 用于公开资料检索、来源整理和结论归纳
  ---

  # Web Research

  当用户要求调研某个主题时：
  1. 明确问题范围
  2. 收集来源
  3. 整理关键事实
  4. 输出带来源的总结
  """

  namespace = ("user-123", "agent-files")

  # 先把 skill 文件写入 LangGraph store,通过 store.put() 主动且独立地把技能文件以键值对的形式存入了数据库中，并绑定了一个叫做 ("user-123", "agent-files") 的命名空间（Namespace）。之后 Agent 运行的时候，中间件会自动去这个指定的数据库命名空间里检索文件
  store.put(
      namespace,
      "/skills/project/web-research/SKILL.md",
      {
          "content": skill_md,
          "encoding": "utf-8",
      },
  )

  backend = StoreBackend(
      #可以传入各种数据库的客户端，MongoDBStore(mongo_client) —— 传入 MongoDB 的连接客户端等
      store=store,
      #强行将该 Agent 的文件系统根目录指向了 `("user-123", "agent-files")` 这一层级。同一个数据库内部划分权限和地盘的。
      namespace=lambda rt: namespace,
  )

  agent = create_deep_agent(
      model="openai:gpt-4.1-mini",
      tools=[],
      backend=backend,
      store=store,
      skills=["/skills/project/"],
  )

  result = agent.invoke(
      {
          "messages": [
              {
                  "role": "user",
                  "content": "帮我调研一下新能源汽车行业趋势，并整理成简短报告",
              }
          ],
      },
      config={
          "configurable": {
              "thread_id": "demo-storebackend-skills"
          }
      },
  )
```

第一段写法虽然简单，但它把技能文件作为 `invoke` 参数随包发送，导致状态与具体的会话线程（Thread）强绑定，无法做到跨会话的技能共享，且每次调用都需要高成本地重复构造文件对象。

而第二种写法引入了 LangGraph 的 `Store` 抽象和 `StoreBackend`。它在架构上实现了**‘存储与执行的彻底解耦’**。我们通过将 Markdown 技能文件预先持久化到指定的 `Namespace` 中，让中间件在运行时按需进行‘指针式’挂载。

这种设计在生产环境中具备极高的平台化价值：首先，它支持跨 `thread_id` 的全局技能共享；其次，通过在生产环境将 `InMemoryStore` 替换为底层的分布式数据库（如 PostgreSQL），能够完美应对云原生容器重启、水平扩容（Horizontal Scaling）时本地磁盘不可靠的痛点，是真正具备弹性（Resilient）的生产级多智能体架构。”

#### 1.2.3 子智能体

在deepagents中，子智能体会作为一个特殊的task/tool，给到主智能体去调用。

子智能体的定义，有两种方式：

可通过声明式的方式来进行定义：

```python
import os
from typing import Literal

from deepagents import create_deep_agent
from tavily import TavilyClient

tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])


def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search"""
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )


research_subagent = {
    "name": "research-agent",
    "description": "Used to research more in depth questions",
    "system_prompt": "You are a great researcher",
    "tools": [internet_search],
    "model": "openai:gpt-5.4",  # Optional override, defaults to main agent model
}
subagents = [research_subagent]

agent = create_deep_agent(
    model="google_genai:gemini-3.5-flash",
    subagents=subagents,
)
```

可通过预编译的CompiledSubAgent来传入：

```python
from deepagents import create_deep_agent, CompiledSubAgent
from langchain.agents import create_agent

# Create a custom agent graph
custom_graph = create_agent(
    model=your_model,
    tools=specialized_tools,
    prompt="You are a specialized agent for data analysis..."
)

# Use it as a custom subagent
custom_subagent = CompiledSubAgent(
    name="data-analyzer",
    #这个描述不是写给人类看的，而是写给主大模型看的。 大模型正是通过这段描述的语义，来判断当前用户的请求（比如“帮我分析一下这份 Excel 报表”）应该分发给哪一个子 Agent。如果描述写得模糊，主 Agent 就容易“指派错任务”。
    description="Specialized agent for complex data analysis tasks",
    #主 Agent 可以是用 Gemini 驱动的，但这个子 Agent 的内部（custom_graph）完全可以用开源的 Llama、或者复杂的 LangGraph 状态机、甚至是一段纯硬编码的 Python 代码。只要它能 invoke 运行，主 Agent 就能通过这个中间件向它分发任务。
    runnable=custom_graph
)

subagents = [custom_subagent]

agent = create_deep_agent(
    model="google_genai:gemini-3.5-flash",
    tools=[internet_search],
    system_prompt=research_instructions,
    subagents=subagents
)
```

也可以传入其他langgraph构建的图，只需要保证图的状态当中有message键即可。

#### **阶段零：对象创建与编译（系统准备期）**

*对应代码执行 `create_deep_agent(...)` 的瞬间。*

- **接管与注册**：主智能体将配置好的子 Agent 列表交给 `SubAgentMiddleware`，中间件在内存中将它们实例化。
- **工具化降维**：中间件调用底层方法，把这群子 Agent 统一编译打包成一个名叫 `task_tool` 的聚合工具，并强行注入到主 Agent 的可用工具列表中。
- **感知注入**：中间件提取所有子 Agent 的“名字”和“描述”，动态拼接到主 Agent 的 `System Prompt` 后面，让主模型提前“认识”自己的团队。

#### **阶段一：会话初始化（运行期起点）**

*对应用户发送第一条消息，触发 `agent.invoke(...)`。*

- **请求接入**：用户的 Message 和 `thread_id` 正式进入系统。
- **共享基建挂载**：`FileSystemMiddleware` 被触发，主 Agent 和所有传入的子 Agent 会共同挂载同一个 `StoreBackend` 实例。系统根据 Namespace 为它们划分好属于当前会话的**共享文件工作区**。

#### **阶段二：主智能体唤醒与环境感知**

- **技能加载**：触发 `SkillsMiddleware`，从虚拟路径（如 `StoreBackend` 中）读取预先配置好的技能书（如 `SKILL.md` 的 SOP）。
- **上下文组装**：此时的主 Agent 已经全副武装——它拥有了业务 SOP（技能书）、手下员工的花名册（拼接好的 Prompt），准备开始处理用户问题。

#### **阶段三：主大模型思考与路由决策**

- **需求评估**：主大模型（如 Gemini）分析用户的复杂问题（例如“调研并分析数据”）。
- **决策“点将”**：主模型发现依靠自身基础能力无法直接处理，于是查阅 `System Prompt` 中的员工花名册，通过语义匹配，决定将任务委派给名为 `data-analyzer` 的子 Agent。

#### **阶段四：中间件拦截与精准路由（核心分发点）**

- **输出调用指令**：主大模型输出标准的 Tool Call 格式：调用 `task_tool`，并附带参数 `subagent_name="data-analyzer"`。
- **底层拦截与映射**：`SubAgentMiddleware` 拦截到这个虚拟工具的调用，顺藤摸瓜，通过名字精确锁定内存中对应的那个真实 `custom_graph`（子智能体实例）。

#### **阶段五：共享空间与指针传递**

- **低成本通信**：为了避免大文件直接传递导致 Token 爆炸，中间件将主 Agent 的当前上下文或关联文件，以“文件路径指针”的形式，平滑共享到第一步建立的同源 `StoreBackend` 空间中。

#### **阶段六：子智能体独立搬砖（执行期）**

- **主从状态切换**：主 Agent 暂时进入挂起（Wait/Suspend）状态。
- **子 Agent 唤醒**：`data-analyzer` 子智能体被正式唤醒。它读取共享空间中的文件，调用自己专属的 specialized_tools（如数据分析工具、代码执行器），在一个隔离的沙盒中独立完成计算和推理。

#### **阶段七：结果回传与全链路闭环**

- **交卷**：子 Agent 执行完毕，生成最终的分析报告。
- **伪装成观察值**：中间件将这份报告包装成 `task_tool` 的执行结果（Observation），回传给主 Agent。
- **汇总结案**：主 Agent 苏醒，接收到“工具”返回的完美答案，将其整合后，最终输出给用户，整个多智能体协作流程圆满闭环。

### 1.3 核心价值

DeepAgents 对本项目的价值主要体现在三个方面。

第一，多智能体开发更自然。官方 Subagents 文档说明，Deep Agents 可以通过 `subagents` 参数配置自定义子智能体，主智能体通过内置 `task` 工具委托任务。

子智能体适合处理会污染主上下文的多步骤任务、需要专门工具的任务，或者需要不同模型能力的任务。本项目正好把“研究管理”和“信息检索”拆开：主智能体负责研究策略和章节写作，检索子智能体负责搜索、网页读取、RAGFlow 检索和事实整理。

第二，规划能力更适合长任务。研究报告不是一次问答，而是“理解任务 -> 设计大纲 -> 拆解章节 -> 检索证据 -> 写正文 -> 保存结果”的链路。

DeepAgents 内置 `write_todos` 规划工具，可以让 Agent 在执行前维护任务清单，并随着缺失章节、检索结果或用户补充要求调整计划。

第三，上下文管理更稳定。官方 Context Engineering 文档把 文件 offloading、summarization 和 subagent isolation 都列为长任务上下文管理机制。

对研究场景来说，搜索结果、网页正文、事实卡片、引用来源和章节草稿都可能很长。如果全部放在消息历史里，模型容易遗漏章节、混淆来源，甚至把搜索摘要当事实。使用虚拟文件系统和子智能体隔离后，主智能体可以只接收整理后的结论和证据结构。

本项目使用deepagents的架构：

```mermaid
flowchart LR


    subgraph 调用架构
        U2[用户任务] --> M[主智能体]
        M --> P[Todo 计划]
        M --> S[检索子智能体]
        S --> T2[专属检索工具]
        T2 --> S
        S --> E[事实和来源]
        E --> M
        M --> DB[逐章节落库]
    end
```

## 2. Agent总设计

当前项目中，仅在大纲制定环节和研究执行环节使用智能体。报告生成阶段暂不引入agent实现。

整个 Agent 链路可以拆成两层：

| 层级   | 名称      | 作用                                        |
| ---- | ------- | ----------------------------------------- |
| 主智能体 | 研究管理智能体 | 理解研究任务、生成大纲、修改大纲、拆解章节、协调检索、整理事实和洞察、写出章节正文 |
| 子智能体 | 信息检索智能体 | 围绕主智能体分派的问题进行公开搜索、网页读取、内部知识库检索和事实整理       |

各个阶段的职责边界如下：

| 阶段         | 是否使用 LLM Agent             | 产物                                                |
| ---------- | -------------------------- | ------------------------------------------------- |
| 生成研究任务书和大纲 | 使用研究管理智能体                  | `research_brief`、`outline`                        |
| 修改大纲       | 使用研究管理智能体                  | 修订后的 `outline`                                    |
| 执行研究       | 使用研究管理智能体 + 信息检索智能体        | `sections`、`sources`、`fact_cards`、`insight_cards` |
| 渲染报告       | 不使用agent，仅使用html渲染器对报告进行渲染 | HTML、目录、引用、参考来源                                   |

这个设计的核心原因是：研究过程需要 LLM 进行理解、拆解、检索规划、归纳和写作；但报告渲染阶段主要是结构转换和页面展示，不应该让 LLM 重新补写事实、改写证据链或生成新的来源。

整体流程如下：

```mermaid
flowchart TD
    后台任务[后台任务]
    Agent门面[ResearchAgent]
    研究管理[研究管理智能体]
    信息检索[信息检索智能体]
    外部搜索[external_search]
    网页读取[read_web_page]
    知识库检索[ragflow_search]
    章节保存[save_research_section]
    项目仓储[(research_projects)]
    报告渲染[write_html_report]
    报告仓储[(report_versions)]

    后台任务 --> Agent门面
    Agent门面 --> 研究管理
    研究管理 --> 信息检索
    信息检索 --> 外部搜索
    信息检索 --> 网页读取
    信息检索 --> 知识库检索
    研究管理 --> 章节保存
    章节保存 --> 项目仓储
    Agent门面 --> 报告渲染
    报告渲染 --> 报告仓储
```

## 3. 研究Agent设计

在前面的架构设计环节，整个研究过程已经拆成两个 Agent：一个是主研究管理者，另一个专门负责收集信息、整理来源并构建事实证据链。

这里要注意一个边界：信息检索智能体不是“帮忙写报告”的智能体，它只负责证据材料；主研究智能体才负责把证据组织成章节正文和研究结果。

### 3.1 主研究智能体的职责

主研究智能体负责：

- 基于用户的问题，构建研究任务书和研究大纲。

- 基于用户对大纲的修改意见，修改大纲。

- 基于已确认大纲，拆解每个章节需要回答的问题。

- 将检索问题分派给信息检索智能体。

- 整理来源、事实卡片、冲突信息和洞察卡片。

- 写出每个章节的完整正文。

- 为关键判断构建证据链。

- 调用 `save_research_section` 工具，将章节研究结果保存至数据库。

主研究智能体不负责：

- 不直接调用互联网搜索工具。

- 不直接读取网页正文。

- 不直接调用 RAGFlow。

- 不直接编写最终 HTML。

- 不调用报告写作 Agent。

- 不保存项目状态和任务状态。

主研究智能体的核心输入输出如下：

| 任务类型                      | 输入                 | 输出         |
| ------------------------- | ------------------ | ---------- |
| `generate_research_brief` | 项目主题、目标、读者、地域和时间范围 | 研究任务书、大纲草案 |
| `revise_outline`          | 当前大纲、用户修改意见        | 修订后的大纲     |
| `generate_report`         | 已确认大纲、项目设定、用户补充要求  | 逐章节保存的研究结果 |

项目中使用 `ResearchAgent` 类作为业务门面，后台任务不直接操作 DeepAgents 对象：

```python
class ResearchAgent:
    """研究智能体业务门面。"""

    def __init__(self, manager_agent: Any | None = None, report_agent: Any | None = None) -> None:
        self.manager_agent = manager_agent
        self.report_agent = report_agent

    async def generate_research_brief(self, project: dict[str, Any] | None) -> ResearchBriefResult:
        payload = self._build_generate_research_brief_input(project=project)
        raw_result = await self._invoke_manager_agent(
            task_name="generate_research_brief",
            payload=payload,
        )
        return self._parse_research_brief_result(raw_result=raw_result, project=project)

    async def revise_outline(
        self,
        project: dict[str, Any] | None,
        outline: list[OutlineNode],
        revision_instruction: str,
    ) -> list[OutlineNode]:
        payload = self._build_revise_outline_input(
            project=project,
            outline=outline,
            revision_instruction=revision_instruction,
        )
        raw_result = await self._invoke_manager_agent(
            task_name="revise_outline",
            payload=payload,
        )
        return self._parse_outline_result(raw_result=raw_result, fallback_outline=outline)
```

研究执行阶段不是一次性要求模型返回一个巨大的 `research_result`，而是要求主智能体逐章节调用工具落库：

```python
async def generate_research_result(
    self,
    project: dict[str, Any] | None,
    outline: list[OutlineNode],
    user_instruction: str | None,
) -> ResearchResult:
    project_id = self._get_project_id(project=project)
    await research_project_repository.clear_research_sections(project_id=project_id)
    expected_section_ids = self._expected_research_section_ids(outline=outline)
    sections: list[dict[str, Any]] = []
    missing_section_ids = sorted(expected_section_ids)

    for attempt in range(1, 5):
        payload = self._build_generate_research_result_input(
            project=project,
            outline=outline,
            user_instruction=user_instruction,
            required_section_ids=sorted(expected_section_ids),
            missing_section_ids=missing_section_ids,
            attempt=attempt,
        )
        await self._invoke_manager_agent(task_name="generate_report", payload=payload)
        sections = await research_project_repository.get_research_sections(project_id=project_id)
        saved_section_ids = {
            str(section.get("section_id"))
            for section in sections
            if isinstance(section, dict) and section.get("section_id")
        }
        missing_section_ids = sorted(expected_section_ids - saved_section_ids)
        if not missing_section_ids:
            break

    return self._build_research_result_from_saved_sections(
        sections=sections,
        sources=await research_project_repository.get_research_sources(project_id=project_id),
        project=project,
        outline=outline,
    )
```

这里有一个重要设计：如果部分章节没有被保存，系统会把缺失的 `section_id` 重新传给主智能体，要求它补写缺失章节。这样可以避免一次大输出中遗漏章节。

主研究智能体的 Prompt 核心片段如下：

```markdown
你是 AI 研究报告工作台中的研究管理智能体。

你的职责是完成研究本身：理解任务、设计大纲、协调信息检索、整理事实、形成洞察、写出完整章节正文，并产出可落库的结构化研究结果。

你不是报告渲染智能体。你不负责把研究结果渲染为最终 HTML。报告渲染由后端确定性渲染流程基于你落库的 `research_result` 完成。
```

针对 `generate_report` 任务，Prompt 明确要求主智能体逐章节保存结果：

```markdown
目标：根据已确认大纲完成逐章节研究，并通过 `save_research_section` 工具把每个有正文的章节写入数据库。不要一次性输出完整 `research_result`。

流程要求：

1. 基于已确认大纲识别需要写正文的章节。
2. 如果任务载荷中存在 `missing_section_ids`，本轮只处理这些章节，不要重写已保存章节。
3. 对每个章节拆解检索问题，委托信息检索智能体获取公开来源和可复核事实。
4. 写出该章节完整正文、关键发现、证据链、表格/图表结构、风险说明和本章来源详情。
5. 调用 `save_research_section(project_id, section)` 保存该章节。
```

### 3.2 信息检索智能体的职责

信息检索智能体负责：

- 基于主研究智能体分派的问题，构造搜索关键词。

- 使用公开互联网搜索工具发现资料来源。

- 使用网页读取工具读取关键网页正文和元数据。

- 按需使用 RAGFlow 工具检索内部知识库。

- 对来源进行去重和相关性判断。

- 从来源中提取可复核事实。

- 标注每条事实对应的来源。

- 识别不同来源之间的冲突、口径差异和不确定性。

信息检索智能体不负责：

- 不设计完整研究大纲。

- 不生成最终 HTML 报告。

- 不编造 URL、日期、机构名称或数据。

- 不把搜索摘要直接当作最终事实。

- 不保存数据库状态。

信息检索智能体可以使用的工具如下：

| 工具                | 文件                             | 作用                  |
| ----------------- | ------------------------------ | ------------------- |
| `external_search` | `app/tools/external_search.py` | 调用 Tavily 搜索公开互联网资料 |
| `read_web_page`   | `app/tools/web_reader.py`      | 读取网页正文、标题、发布时间线索    |
| `ragflow_search`  | `app/tools/ragflow_search.py`  | 检索 RAGFlow 内部知识库    |

公开互联网搜索工具的核心结构如下：

```python
async def external_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    time_range: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    normalized_query = query.strip()
    if not normalized_query:
        return {
            "status": "error",
            "provider": "tavily",
            "query": query,
            "results": [],
            "error": "query 不能为空",
        }

    settings = get_settings()
    if not settings.tavily_api_key:
        return {
            "status": "skipped",
            "provider": "tavily",
            "query": normalized_query,
            "results": [],
            "error": "TAVILY_API_KEY 未配置",
        }
```

网页读取工具的核心结构如下：

```python
async def read_web_page(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    normalized_url = url.strip()
    if not normalized_url.startswith(("http://", "https://")):
        return {
            "status": "error",
            "url": url,
            "title": None,
            "published_at": None,
            "content": "",
            "error": "仅支持 http 或 https URL",
        }

    html, final_url, content_type = await asyncio.to_thread(_fetch_html, normalized_url)
```

RAGFlow 检索工具的核心结构如下：

```python
async def ragflow_search(
    query: str,
    dataset_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    page: int = 1,
    page_size: int = 10,
    similarity_threshold: float = 0.2,
    vector_similarity_weight: float = 0.3,
    top_k: int = 1024,
    keyword: bool = False,
) -> dict[str, Any]:
    normalized_query = query.strip()
    if not normalized_query:
        return {
            "status": "error",
            "provider": "ragflow",
            "query": query,
            "chunks": [],
            "error": "query 不能为空",
        }
```

信息检索智能体的 Prompt 核心片段如下：

```markdown
你是 AI 研究报告工作台中的信息检索智能体。

你的职责是围绕研究管理智能体分派的问题进行资料检索、网页读取、内部知识库检索、事实整理和证据链输出。

搜索工具用于发现来源，不把搜索摘要直接当作最终事实。
网页读取工具用于获取可追溯正文和来源元数据。
RAGFlow 工具用于检索内部知识库，不把内部资料伪装成公开来源。
```

信息检索智能体的最终输出必须是严格 JSON：

```json
{
  "sources": [
    {
      "source_id": "source-1",
      "title": "来源标题",
      "url": "https://example.com",
      "published_at": "2026-01-01",
      "source_type": "public_web",
      "summary": "来源摘要"
    }
  ],
  "fact_cards": [
    {
      "fact_id": "fact-1",
      "statement": "可复核事实",
      "source_ids": ["source-1"],
      "confidence": "medium",
      "evidence_summary": "证据摘要"
    }
  ],
  "conflicts": []
}
```

### 3.3 其他可扩展的智能体

在实际生产环境下，本项目还可以继续扩展更多智能体，但第一版不需要一次性拆太多 Agent。拆分智能体的原则是：只有当某类任务有独立工具、独立上下文和独立输出结构时，才适合拆成单独智能体。

可扩展方向包括：

- 问数智能体：面向企业内部结构化数据库，负责 SQL 生成、指标查询和数据解释。

- 竞品分析智能体：专门跟踪公司、产品、融资、价格、渠道和客户案例。

- 政策分析智能体：专门检索政策文件、监管动态、官方解读和政策影响。

- 财务分析智能体：专门处理财报、公告、经营数据和估值指标。

- 图表规划智能体：根据研究结果规划适合展示的图表类型和数据结构。

这些智能体都不应该改变当前系统的主边界：研究管理智能体负责协调研究过程，报告最终由确定性渲染流程生成。

## 4. 开发

### 4.1 多智能体架构的难点

多智能体架构的难点不在于“创建多个 Agent”，而在于职责边界和数据边界是否清晰。

本项目需要处理几个问题：

1. 谁负责拆解研究问题？

2. 谁负责检索资料？

3. 谁负责判断来源是否可用？

4. 谁负责把事实写成章节正文？

5. 谁负责保存研究结果？

6. 谁负责生成最终 HTML？

如果边界不清晰，很容易出现以下问题：

| 问题                | 结果                   |
| ----------------- | -------------------- |
| 主智能体也搜索，检索智能体也写报告 | 职责混乱，输出不可控           |
| 报告阶段继续让 LLM 补内容   | 来源和事实链条断裂            |
| 所有结果一次性塞进上下文      | 内容过长，模型容易遗漏章节        |
| 只返回自然语言结果         | 后端无法稳定落库和渲染          |
| 工具返回异常没有结构化       | Agent 不知道应该重试、跳过还是降级 |

因此，本项目采用以下约束：

- 主研究智能体只协调研究过程，不直接执行搜索和网页读取。

- 信息检索智能体只处理来源、事实和冲突，不写最终报告。

- 主研究智能体必须通过 `save_research_section` 工具逐章节落库。

- 报告渲染工具只做 HTML 展示转换，不新增事实、来源和结论。

- 所有关键产物都使用 Pydantic 结构描述。

核心结构包括：

```python
class FactCard(BaseModel):
    fact_id: str
    statement: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: str = "medium"


class InsightCard(BaseModel):
    insight_id: str
    title: str
    summary: str
    supporting_fact_ids: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    claim: str
    fact_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    confidence: str = "medium"
```

章节研究结果结构如下：

```python
class ResearchSection(BaseModel):
    section_id: str
    title: str
    summary: str | None = None
    body: str
    key_findings: list[str] = Field(default_factory=list)
    evidence_chain: list[EvidenceItem] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    charts: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
```

完整研究结果结构如下：

```python
class ResearchResult(BaseModel):
    title: str
    executive_summary: str | None = None
    sections: list[ResearchSection] = Field(default_factory=list)
    sources: list[ReportSource] = Field(default_factory=list)
    fact_cards: list[FactCard] = Field(default_factory=list)
    insight_cards: list[InsightCard] = Field(default_factory=list)
    synthesis: ResearchSynthesis | None = None
```

这些结构就是研究阶段和报告渲染阶段之间的边界对象。

### 4.2 DeepAgents框架的介绍

DeepAgents 用于构建能够规划任务、调用工具、委托子智能体并维护上下文文件系统的 Agent。

在本项目中，DeepAgents 主要提供四类能力：

| 能力   | 在本项目中的作用                                                                      |
| ---- | ----------------------------------------------------------------------------- |
| 主智能体 | 构建研究管理智能体                                                                     |
| 子智能体 | 构建信息检索智能体                                                                     |
| 工具调用 | 调用 `save_research_section`、`external_search`、`read_web_page`、`ragflow_search` |
| 文件系统 | 把大规模检索结果、来源列表和中间材料写入 `/research/workspace/`，避免上下文膨胀                           |

主智能体构建代码如下：

```python
def _build_deepagents_manager_agent() -> Any | None:
    settings: Settings = get_settings()
    model_name = _build_model_name(settings=settings)
    subagents = [_build_search_subagent(model_name=model_name)]
    return create_deep_agent(
        model=model_name,
        tools=[save_research_section],
        system_prompt=_load_prompt(RESEARCH_MANAGER_PROMPT_PATH),
        subagents=subagents,
        name="research-manager-agent",
        checkpointer=MemorySaver(),
    )
```

信息检索子智能体构建代码如下：

```python
def _build_search_subagent(model_name: str) -> dict[str, Any]:
    settings: Settings = get_settings()
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

Prompt 不写死在代码中，而是维护在外部 Markdown 文件：

```python
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
RESEARCH_MANAGER_PROMPT_PATH = PROMPT_DIR / "research_manager.md"
SEARCH_AGENT_PROMPT_PATH = PROMPT_DIR / "search_agent.md"

def _load_prompt(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8").strip()
```

模型名称通过配置生成：

```python
def _build_model_name(settings: Settings) -> str:
    provider = settings.llm_provider.lower()
    if provider == "deepseek":
        return f"deepseek:{settings.llm_model_name}"
    if provider == "openai":
        return f"openai:{settings.llm_model_name}"
    return f"{provider}:{settings.llm_model_name}"
```

调用 DeepAgents 时，大 payload 不直接塞进消息正文，而是写入虚拟文件系统：

```python
def _build_deepagents_input(self, payload: dict[str, Any]) -> dict[str, Any]:
    task_json = json.dumps(payload, ensure_ascii=False, indent=2, default=self._json_default)
    return {
        "messages": [
            {
                "role": "user",
                "content": (
                    "请执行 /research/task_payload.json 中的研究任务。"
                    "先使用 todo 规划步骤；大规模检索结果和报告中间稿请写入"
                    " /research/workspace/ 下的文件；最终只返回严格 JSON。"
                ),
            }
        ],
        "files": {
            "/research/task_payload.json": create_file_data(task_json),
            "/research/workspace/README.md": create_file_data(
                "该目录用于保存检索摘要、来源整理、事实卡片、洞察卡片和报告草稿。"
            ),
        },
    }
```

这里的设计重点是：消息里只告诉智能体任务文件路径，大量输入数据放到 `/research/task_payload.json`，中间材料放到 `/research/workspace/`。

### 4.3 编码

Agent 编码可以按下面的顺序实现：

1. 定义结构化输出模型。

2. 编写工具函数。

3. 编写 Prompt 文件。

4. 构建 DeepAgents 主智能体和子智能体。

5. 编写 `ResearchAgent` 业务门面。

6. 在后台任务中调用 `ResearchAgent`。

#### 1. 定义结构化输出模型

研究任务书结构：

```python
class ResearchBrief(BaseModel):
    topic: str
    research_goal: str
    target_audience: str
    scope_summary: str
    key_questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
```

研究任务书和大纲生成结果：

```python
class ResearchBriefResult(BaseModel):
    research_brief: ResearchBrief
    outline: list[OutlineNode]
```

报告渲染结果：

```python
class ReportGenerationResult(BaseModel):
    title: str
    html: str
    sources: list[ReportSource] = Field(default_factory=list)
    fact_cards: list[FactCard] = Field(default_factory=list)
    insight_cards: list[InsightCard] = Field(default_factory=list)
```

#### 2. 编写章节保存工具

主研究智能体生成章节正文后，需要调用 `save_research_section` 保存章节。这个工具会校验章节是否完整，避免占位内容进入报告。

```python
async def save_research_section(project_id: str, section: dict[str, Any]) -> dict[str, Any]:
    errors = await _validate_section(project_id=project_id, section=section)
    if errors:
        return {"ok": False, "errors": errors}

    normalized_sources = await _normalize_section_sources(
        project_id=project_id,
        section=section,
    )
    normalized_section = _normalize_section(section, sources=normalized_sources)
    await research_project_repository.upsert_research_section(
        project_id=project_id,
        section=normalized_section,
    )
    if normalized_sources:
        await research_project_repository.upsert_research_sources(
            project_id=project_id,
            sources=normalized_sources,
        )
    return {
        "ok": True,
        "project_id": project_id,
        "section_id": normalized_section["section_id"],
        "sources_saved": len(normalized_sources),
        "message": "research section saved",
    }
```

工具校验规则包括：

- `section_id` 必须存在，并且属于已确认大纲。

- `body` 必须是完整正文，不能是占位文案。

- `key_findings` 至少有一条非空关键发现。

- `evidence_chain` 至少有一条证据链。

- `evidence_chain.source_ids` 引用的来源必须能在 `section.sources` 或项目已有来源中找到。

#### 3. 构建 Agent 单例

后台任务通过 `get_research_agent()` 获取当前进程内复用的 Agent 门面：

```python
_research_agent: ResearchAgent | None = None

def build_research_agent() -> ResearchAgent:
    manager_agent = _build_deepagents_manager_agent()
    return ResearchAgent(manager_agent=manager_agent, report_agent=None)

def get_research_agent() -> ResearchAgent:
    global _research_agent
    if _research_agent is None:
        _research_agent = build_research_agent()
        logger.info("研究智能体门面已初始化")
    return _research_agent
```

#### 4. 后台任务调用 Agent

生成大纲任务中调用 `generate_research_brief`：

```python
project = await research_project_repository.get_project(project_id=project_id)
research_agent = get_research_agent()
result = await research_agent.generate_research_brief(project=project)

await research_project_repository.save_research_brief_and_outline(
    project_id=project_id,
    research_brief=result.research_brief,
    outline=result.outline,
)
```

生成报告任务中先执行研究，再渲染报告：

```python
project = await research_project_repository.get_project(project_id=project_id)
outline = await research_project_repository.get_confirmed_outline(project_id=project_id)
research_agent = get_research_agent()

research_result = await research_agent.generate_research_result(
    project=project,
    outline=outline,
    user_instruction=user_instruction,
)
await research_project_repository.save_research_result(
    project_id=project_id,
    research_result=research_result,
)

project_with_research_result = await research_project_repository.get_project(project_id=project_id)
result = await research_agent.generate_report(
    project=project_with_research_result,
    outline=outline,
    user_instruction=user_instruction,
)
await report_repository.save_report_version(
    project_id=project_id,
    title=result.title,
    html=result.html,
    sources=result.sources,
)
```

#### 5. 确定性报告渲染

`generate_report` 方法内部不调用报告写作 Agent，而是调用 `write_html_report`：

```python
async def generate_report(
    self,
    project: dict[str, Any] | None,
    outline: list[OutlineNode],
    user_instruction: str | None,
) -> ReportGenerationResult:
    payload = self._build_generate_report_input(
        project=project,
        outline=outline,
        user_instruction=user_instruction,
    )
    raw_result = await write_html_report(
        research_result=payload["research_result"],
        layout_plan=self._build_default_layout_plan(payload=payload),
    )
    return self._parse_report_generation_result(raw_result=raw_result, project=project)
```

报告渲染工具的边界非常明确：

```python
async def write_html_report(
    research_result: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    **legacy_kwargs: Any,
) -> dict[str, Any]:
    document_ir = await build_report_document(
        research_result=research_result,
        layout_plan=layout_plan,
    )
    return await render_report_html(document_ir=document_ir)
```

它只做三件事：

1. 把 `research_result` 转成展示用 document IR。

2. 渲染 HTML、目录、章节、引用和来源列表。

3. 返回 `title`、`html` 和 `sources`。

它不做以下事情：

- 不新增事实。

- 不新增来源。

- 不新增结论。

- 不调用搜索工具。

- 不改写证据链。

最终，Agent 开发完成后，整个链路是：

```text
background
  -> get_research_agent()
  -> generate_research_brief / revise_outline / generate_research_result
  -> DeepAgents 研究管理智能体
  -> DeepAgents 信息检索子智能体
  -> tools 检索和读取资料
  -> save_research_section 逐章节落库
  -> write_html_report 确定性渲染
  -> report_repository 保存报告版本
```
