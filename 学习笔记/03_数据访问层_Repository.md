# 模块 3：数据访问层 — Repository

## 涉及文件

- `app/repository/mongodb.py` —— MongoDB 连接管理
- `app/repository/research_project_repository.py` —— 研究项目 CRUD
- `app/repository/research_task_repository.py` —— 后台任务状态管理
- `app/repository/report_repository.py` —— 报告版本管理
- `app/repository/report_storage.py` —— 报告 HTML 对象存储抽象

---

## 3.1 MongoDB 连接管理（`mongodb.py`）

### 单例客户端模式

```python
_mongodb_client: AsyncMongoClient | None = None

def get_mongodb_client() -> AsyncMongoClient:
    global _mongodb_client
    if _mongodb_client is None:
        settings = get_settings()
        _mongodb_client = AsyncMongoClient(
            settings.mongodb_uri,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=5000,
        )
    return _mongodb_client
```

**为什么用模块级单例**：PyMongo 的 `AsyncMongoClient` 内部已经维护了连接池，不需要每次请求创建新客户端。模块级全局变量 + `None` 检查实现了懒加载——只有在第一次调用 `get_mongodb_client()` 时才建立连接，而不是 import 时就连接。

`serverSelectionTimeoutMS=5000` 意味着如果 MongoDB 不可达，5 秒后超时报错，不会无限等待。

### 数据库对象获取

```python
def get_mongodb_database() -> AsyncDatabase:
    settings = get_settings()
    return get_mongodb_client()[settings.mongodb_database]
```

每个 repository 模块通过 `get_mongodb_database()[collection_name]` 获取集合对象。数据库名由配置项 `mongodb_database` 决定（默认 `deep_research`）。

### 生命周期管理

```python
async def ping_mongodb() -> None:
    await get_mongodb_database().command("ping")

async def close_mongodb_client() -> None:
    global _mongodb_client
    if _mongodb_client is not None:
        _mongodb_client.close()
        _mongodb_client = None
```

当前 FastAPI 应用没有显式的 lifespan 管理，`close_mongodb_client` 和 `ping_mongodb` 已定义但未被调用——这是后续可以完善的扩展点。

---

## 3.2 研究项目 Repository

### 文档结构

一个研究项目在 MongoDB 中是一行 document：

```json
{
  "_id": "项目编号",
  "project_id": "项目编号",
  "topic": "研究主题",
  "request": {  },
  "status": "outline_ready",
  "outline": [  ],
  "confirmed_outline": [  ],
  "research_brief": {  },
  "research_result": {  },
  "sections": [  ],
  "sources": [  ],
  "fact_cards": [  ],
  "insight_cards": [  ],
  "created_at": "...",
  "updated_at": "..."
}
```

所有数据都存在一个 document 里，不用关联查询。这种"宽表"设计适合 MongoDB 的文档模型——一个项目的所有信息都在同一个对象中。

### 数据清洗：`_clean_document`

```python
def _clean_document(document: dict | None) -> dict | None:
    if document is None:
        return None
    document.pop("_id", None)         # 去掉 MongoDB 内部 _id
    if "status" in document:
        document["status"] = ProjectStatus(str(document["status"]))  # 字符串 → 枚举
    return document
```

从 MongoDB 读出的 `status` 是字符串，需要转回 `ProjectStatus` 枚举。上层代码拿到的是枚举值，可以做类型安全的比较（`project["status"] == ProjectStatus.OUTLINE_READY`）。

### 类型转换工具函数

```python
def _dump_outline(outline: list[OutlineNode] | list[dict]) -> list[dict]:
    for node in outline:
        if isinstance(node, OutlineNode):
            dumped_outline.append(node.model_dump(mode="python"))  # Pydantic → dict
        else:
            dumped_outline.append(node)  # 已是 dict
    return dumped_outline
```

兼容 Pydantic 对象和普通 dict 两种输入。Agent 返回的可能是 `OutlineNode` 对象（通过 `model_validate` 转换后），也可能直接是 dict（从 MongoDB 读出来的），这个函数两种都能处理。

### 核心操作

**创建项目**：

```python
async def create_project(project_id, request, topic, status, created_at):
    document = {
        "_id": project_id,
        "outline": [],
        "confirmed_outline": [],
        "research_brief": None,
        "research_result": None,
        "sections": [],
        "sources": [],
        "fact_cards": [],
        "insight_cards": [],
        # ...
    }
    await _get_collection().insert_one(document)
```

所有数组字段初始化为 `[]`，对象字段初始化为 `None`，后续逐步填充。

**逐章节 UPSERT（核心设计）**：

```python
async def upsert_research_section(project_id, section):
    section_id = section.get("section_id")
    # 第一步：删除旧版本
    await _get_collection().update_one(
        {"project_id": project_id},
        {"$pull": {"sections": {"section_id": section_id}}},
    )
    # 第二步：插入新版本
    await _get_collection().update_one(
        {"project_id": project_id},
        {"$push": {"sections": section}, "$set": {"updated_at": utc_now()}},
    )
```

两步操作实现 UPSERT 而不是 `$set` 整个数组的原因：
- Agent 可能多次重写同一章节（校验失败后修正），每次只替换一个元素比更新整个数组更高效
- `$pull` + `$push` 是原子操作组合，不会出现并发写覆盖
- 每个章节独立更新，不同章节之间互不干扰

**研究来源去重合并**：

```python
async def upsert_research_sources(project_id, sources):
    for source in sources:
        # 按 source_id 去重
        if source.get("source_id"):
            await _get_collection().update_one(
                {"project_id": project_id},
                {"$pull": {"sources": {"source_id": source.get("source_id")}}},
            )
        # 按 url 去重
        if source.get("url"):
            await _get_collection().update_one(
                {"project_id": project_id},
                {"$pull": {"sources": {"url": source.get("url")}}},
            )
        # 插入
        await _get_collection().update_one(
            {"project_id": project_id},
            {"$push": {"sources": source}},
        )
```

同一个项目不同章节可能引用相同的来源。先 `$pull` 按 `source_id` 和 `url` 去旧再 `$push`，避免重复条目。

**清空研究数据**：

```python
async def clear_research_sections(project_id):
    await _get_collection().update_one(
        {"project_id": project_id},
        {"$set": {
            "sections": [], "sources": [], "fact_cards": [],
            "insight_cards": [], "research_result": None,
        }},
    )
```

重新生成报告时，先把之前的研究章节、来源、事实卡片、洞察卡片全部清空。这防止了旧数据和新数据混在一起。

### 数据读取方法

```python
async def get_research_sections(project_id) -> list[dict]:
    document = await _get_collection().find_one(
        {"project_id": project_id},
        {"sections": 1}  # projection: 只返回 sections 字段
    )
    sections = [s for s in document.get("sections", []) if isinstance(s, dict)]
    return sorted(sections, key=lambda s: str(s.get("section_id") or ""))
```

按 `section_id` 字符串排序确保返回顺序稳定。`projection={"sections": 1}` 只取需要的字段，减少网络传输。

---

## 3.3 后台任务 Repository（`research_task_repository.py`）

职责单纯：任务状态的 CRUD。需要注意的是任务 ID 既作为 `task_id` 字段也作为 MongoDB 的 `_id`：

```python
document = task.model_dump(mode="python")
document["_id"] = task_id
await _get_collection().insert_one(document)
```

状态更新方法是一层薄封装：

```python
async def mark_task_running(task_id, message):
    await _update_task_status(task_id, TaskStatus.RUNNING, message)

async def _update_task_status(task_id, status, message):
    await _get_collection().update_one(
        {"task_id": task_id},
        {"$set": {"status": status, "message": message, "updated_at": utc_now()}},
    )
```

三个公开方法（`mark_task_running`、`mark_task_succeeded`、`mark_task_failed`）都委托同一个 `_update_task_status`，避免重复写 `$set` 逻辑。

---

## 3.4 报告版本 Repository（`report_repository.py`）

### 版本号自增

```python
async def save_report_version(project_id, title, html, sources):
    latest = await _get_collection().find_one(
        {"project_id": project_id},
        sort=[("version", -1)],
        projection={"version": 1},
    )
    next_version = int(latest["version"]) + 1 if latest else 1
```

每次保存报告递增版本号。不依赖 MongoDB 的自增 ID，而是查当前最大版本号 + 1。

### HTML 正文分离存储

```python
stored_object = await get_report_object_storage().save_html(
    project_id=project_id, report_id=report_id,
    version=next_version, html=html,
)
document["html_uri"] = stored_object.uri   # 只存 URI 引用
document["html_path"] = stored_object.path
document["html_size"] = stored_object.size
```

HTML 正文不存入 MongoDB document，而是通过对象存储接口存到文件系统（或未来的 MinIO）。MongoDB 中只保存存储 URI。原因：
- HTML 报告可能很大（数万字符），存入 MongoDB 会增大文档体积
- 分离存储方便后续切换存储后端
- 读取时可以按需加载

### 读取时自动加载 HTML

```python
async def _load_report_html(document):
    html_uri = document.get("html_uri")
    if isinstance(html_uri, str) and html_uri.strip():
        return await get_report_object_storage().read_html(uri=html_uri)
    return str(document.get("html") or "")  # 兼容旧版内嵌 HTML
```

优先从对象存储读取，兼容旧版本数据（HTML 直接存在 MongoDB document 中）。

---

## 3.5 对象存储抽象（`report_storage.py`）

### Protocol 设计

```python
class ReportObjectStorage(Protocol):
    async def save_html(self, project_id, report_id, version, html) -> StoredReportObject: ...
    async def read_html(self, uri: str) -> str: ...
```

`Protocol` 是 Python 的结构化类型（structural typing），任何实现了这两个方法的类都满足这个接口，不需要显式继承。好处是：
- 不引入 ABC 的运行时开销
- 更灵活：`LocalReportObjectStorage` 和 `MinioReportObjectStorage` 各自独立实现

### 本地文件实现

```python
class LocalReportObjectStorage:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)

    async def save_html(self, ...):
        relative_path = Path(project_id) / f"v{version}-{report_id}.html"
        target_path = self.root_dir / relative_path
        await asyncio.to_thread(self._write_text, target_path, html)
        return StoredReportObject(
            uri=f"local://{self.root_dir.as_posix()}/{relative_path.as_posix()}",
            path=target_path.as_posix(),
            size=len(html.encode("utf-8")),
        )
```

文件写入用 `asyncio.to_thread` 放到线程池执行，不阻塞事件循环。URI 格式为 `local://完整路径`，后续读取时解析 `local://` 前缀提取路径。

### 工厂函数

```python
def get_report_object_storage() -> ReportObjectStorage:
    settings = get_settings()
    if settings.report_storage_backend == "local":
        return LocalReportObjectStorage(root_dir=settings.report_storage_local_dir)
    if settings.report_storage_backend == "minio":
        return MinioReportObjectStorage()  # 尚未实现
    raise ValueError(f"不支持的报告存储后端: {settings.report_storage_backend}")
```

通过配置项 `REPORT_STORAGE_BACKEND` 切换存储后端，默认 `local`。

---

## 3.6 Repository 层的设计原则

| 原则 | 体现 |
|------|------|
| 薄封装 | Repository 方法直接操作 MongoDB，不做业务逻辑。校验、转换、编排都在上层 |
| 隐藏 `_id` | `_clean_document` 自动去掉 MongoDB 内部 `_id`，上层代码不接触 |
| 类型兼容 | `_dump_outline` / `_dump_sources` 兼容 Pydantic 对象和 dict，降低调用方的心智负担 |
| Projection | 读操作使用 projection 只取需要的字段，减少带宽 |
| 字段级更新 | 用 `$set`/`$push`/`$pull` 更新特定字段，不整文档替换 |
| 存储分离 | HTML 正文不存入 MongoDB，通过对象存储接口独立管理 |
