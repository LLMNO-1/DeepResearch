# 模块 8：Agent 工具层 — Tools

## 涉及文件

- `app/tools/external_search.py` —— Tavily 公开搜索
- `app/tools/web_reader.py` —— 网页正文提取
- `app/tools/ragflow_search.py` —— RAGFlow 内部知识库检索
- `app/tools/research_workspace.py` —— 研究章节落库工具
- `app/tools/report_writer.py` —— 报告 HTML 渲染（模块 9 详述）

---

## 8.1 工具函数的统一设计模式

所有工具函数遵循相同的模式：

```python
async def tool_name(...) -> dict[str, Any]:
    # 1. 输入归一化
    normalized = input.strip()

    # 2. 前置校验（优雅降级，不抛异常）
    if not configured:
        return {"status": "skipped", ...}  # 配置缺失 → 跳过

    # 3. 执行外部调用
    try:
        result = await external_api_call(...)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}  # 失败 → 返回错误字典

    # 4. 结果归一化
    return {"status": "ok", "results": normalized_results}
```

**关键设计选择**：工具函数**从不抛异常**。任何错误都封装在返回值字典里（`status: skipped/error/ok`）。因为抛异常会中断 Agent 的执行链路，而返回错误字典可以让 Agent 自行判断如何降级（跳过这个来源、降低置信度等）。

---

## 8.2 公开搜索（`external_search.py`）

### Tavily API 集成

```python
async def external_search(query, max_results=5, search_depth="basic",
                          include_domains=None, exclude_domains=None,
                          time_range=None, start_date=None, end_date=None):
```

支持的过滤条件：
- `max_results`：1-10，默认 5
- `search_depth`：`basic` 或 `advanced`
- `include_domains / exclude_domains`：限定或排除特定域名
- `time_range / start_date / end_date`：时间过滤

### 未配置时的降级

```python
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

配置缺失不抛异常，返回 `skipped` 状态。Agent 收到此结果后可以自主决定是否仅依赖内部知识库、降低报告置信度，还是告知用户需要配置 API Key。

### 结果归一化

```python
def _normalize_tavily_result(item):
    return {
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("published_date"),
        "score": item.get("score"),
        "content": item.get("content"),
        "source_type": "public_web",
    }
```

将 Tavily 的原始字段映射为统一的内部格式。如果将来替换搜索提供商（如换成 SerpAPI），只需改这一个函数。

---

## 8.3 网页读取（`web_reader.py`）

### 轻量实现

没有用 Selenium 或 Playwright——直接 `urllib.request` + 自定义 `HTMLParser`：

```python
async def read_web_page(url, max_chars=12000):
    html, final_url, content_type = await asyncio.to_thread(_fetch_html, url)
    parser = _ReadableHtmlParser()
    parser.feed(html)
    content = " ".join(parser.text_parts)
    limited = content[:max_chars]
    return {
        "status": "ok",
        "url": final_url,
        "title": parser.title,
        "published_at": parser.published_at,
        "content": limited,      # 截断到 max_chars
        "truncated": len(content) > len(limited),
    }
```

**为什么不直接用 BeautifulSoup**：
- 减少依赖（Python 标准库就够了）
- 这个项目不需要精确的 DOM 解析，只需要提取正文文本
- HTMLParser 足够轻量

### 正文提取策略

`_ReadableHtmlParser` 跳过噪音标签（`script`、`style`、`nav`、`footer`），只收集 `<title>` 和正文部分。用 `_ignored_depth` 计数器处理嵌套的忽略标签。

### 发布时间提取

两阶段提取：
1. 从 `<meta>` 标签中找 `article:published_time` / `datePublished` / `pubdate`
2. 从 HTML 源码中正则匹配日期格式（`YYYY-MM-DD`）

```python
def _extract_published_at(html):
    # 第一阶段：LD+JSON datePublished
    pattern1 = r'"datePublished"\s*:\s*"([^"]+)"'
    # 第二阶段：通用日期正则
    pattern2 = r"(\d{4}-\d{2}-\d{2}(?:[T ][0-9:]+(?:Z|[+-]\d{2}:?\d{2})?)?)"
```

### 同步 IO 的处理

```python
html, final_url, content_type = await asyncio.to_thread(_fetch_html, url)
```

`urllib` 是同步 IO。`asyncio.to_thread` 把它放到线程池执行，不阻塞事件循环。这是处理同步库在异步项目中的标准做法。

---

## 8.4 RAGFlow 检索（`ragflow_search.py`）

### API 调用

```python
async def ragflow_search(query, dataset_ids=None, document_ids=None,
                          page=1, page_size=10, similarity_threshold=0.2,
                          vector_similarity_weight=0.3, top_k=1024, keyword=False):
```

参数说明：
- `dataset_ids / document_ids`：至少需要提供一项
- `similarity_threshold`：0.0-1.0，控制召回阈值
- `vector_similarity_weight`：向量相似度和关键词匹配的权重比例
- `top_k`：参与重排序的候选数量

### 多结构兼容

RAGFlow 的不同版本可能返回不同的数据结构。`_extract_chunks` 做了多层兼容：

```python
def _extract_chunks(response_data):
    data = response_data.get("data", response_data)
    if isinstance(data, dict):
        return data.get("chunks") or data.get("docs") or data.get("documents") or []
    if isinstance(data, list):
        return data
    return []
```

先找 `data.chunks`，再找 `data.docs`，再找 `data.documents`，最后兜底 `data` 本身是列表的情况。

### 字段归一化

```python
def _normalize_chunk(item):
    return {
        "chunk_id": item.get("id") or item.get("chunk_id"),
        "content": item.get("content") or item.get("text") or item.get("chunk") or "",
        "score": item.get("similarity") or item.get("score"),
        "source_type": "internal_knowledge_base",
        # ...
    }
```

同一个语义字段在不同版本可能叫 `content`、`text` 或 `chunk`——归一化后上层代码不需要知道 API 版本的差异。

---

## 8.5 研究章节落库（`research_workspace.py`）

这是唯一一个**具有副作用**（写入数据库）的工具，也是 Agent 闭环的关键。

### save_research_section

```python
async def save_research_section(project_id, section) -> dict:
    errors = await _validate_section(project_id, section)
    if errors:
        return {"ok": False, "errors": errors}

    normalized_section = _normalize_section(section, sources=normalized_sources)
    await research_project_repository.upsert_research_section(project_id, normalized_section)
    await research_project_repository.upsert_research_sources(project_id, normalized_sources)

    return {"ok": True, "project_id": project_id, "section_id": section_id,
            "sources_saved": len(normalized_sources), "message": "research section saved"}
```

### 校验规则（_validate_section）

这是对 Agent 输出的质量把关，校验约 20 条规则：

| 校验项 | 规则 | 错误信息示例 |
|--------|------|-------------|
| section 类型 | 必须是 dict | "section 必须是对象" |
| project 存在性 | project_id 必须在数据库中存在 | "project_id 不存在" |
| section_id 合法性 | 必须在已确认大纲的 node_id 集合中 | "section.section_id 不在已确认大纲中" |
| 正文长度 | body ≥ 120 字符 | "必须是完整章节正文，长度至少 120 字符" |
| 占位检测 | body 不含"占位""待生成"等 | "包含占位或待补充文案" |
| 关键发现 | 至少 1 条非空 | "至少需要 1 条非空关键发现" |
| 证据链 | 至少 1 条 | "至少需要 1 条证据链" |
| 来源完整性 | evidence_chain 的 source_id 都在 sources 中 | "缺少以下 source_id 的来源详情" |
| 公开来源 URL | source_type ≠ internal_knowledge_base 时需要 URL | "公开来源必须提供 http(s) URL" |
| 占位风险 | risks 数组不含占位文案 | "包含占位文案" |

### 错误为 Agent 提供修正引导

```json
// 校验失败时的返回
{"ok": false, "errors": [
    "section.body 必须是完整章节正文，长度至少 120 字符",
    "section.key_findings 至少需要 1 条非空关键发现",
    "section.sources 缺少以下 source_id 的来源详情: source-3, source-5"
]}
```

每个错误都是一条具体的修正指令。Agent 在 Prompt 中被要求："如果工具返回 `ok=false`，必须根据 `errors` 修正该章节并再次调用工具，直到保存成功。"

### 来源去重与合并（_normalize_section_sources）

```python
async def _normalize_section_sources(project_id, section):
    incoming_sources = _normalize_sources(section.get("sources"))
    referenced_source_ids = _referenced_source_ids(section.get("evidence_chain"))

    # 如果本章来源不全，从项目已有来源中补全
    missing = referenced_source_ids - set(sources_by_id)
    if missing:
        existing_sources = await get_project(project_id).get("sources")
        for source in existing_sources:
            if source.source_id in missing:
                sources_by_id[source.source_id] = source

    # 去重合并
    return ordered_sources
```

Agent 可能在一个章节中引用先前章节已经引入的来源（相同的 source_id）。这个方法从全局来源池中补全，不对同一来源重复存储。

---

## 8.6 工具函数的注册机制

工具函数直接作为 DeepAgents 的 `tools` 参数传入：

```python
# 主 Agent 的工具
manager_agent = create_deep_agent(
    tools=[save_research_section],  # 只有一个工具
    ...
)

# 子 Agent 的工具
subagent = {
    "tools": [external_search, read_web_page, ragflow_search],  # 2-3 个工具
    ...
}
```

DeepAgents 框架会自动根据函数的类型注解（参数类型 + 返回类型）生成 tool schema。不需要手写 JSON Schema 或装饰器，函数签名就是工具定义。

框架在处理 Agent 的 function call 时，会把函数名、参数描述传给 LLM，LLM 返回要调用的函数名和参数，框架负责执行并返回结果。
