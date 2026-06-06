# 轻量级 arXiv 论文追踪桌面应用设计文档

## 1. 文档目的

本文档用于明确第一版桌面应用的技术选型、架构边界、数据契约与实现约束，作为后续开发、评审和迭代的基础依据。

当前主技术栈确认为：

- Python
- Flet
- sqlite3
- httpx
- feedparser
- pydantic
- python-dateutil
- python-dotenv

本文档以 MVP 为目标，优先支持：

- 按订阅主题拉取 arXiv 最新论文
- 本地缓存论文元数据
- 提供桌面端浏览、筛选、标记和查看详情能力
- 为后续接入第三方 LLM 分析能力预留扩展点

## 2. 产品目标与范围

### 2.1 核心目标

构建一个本地优先、轻量、可扩展的桌面应用，用于持续跟踪 arXiv 某些研究方向的最新论文动态，并提供基础的信息整理能力。

### 2.2 MVP 范围

MVP 包含以下能力：

- 主题订阅管理
- 按订阅主题增量拉取 arXiv 元数据
- 本地持久化论文与同步记录
- 论文列表展示与多条件筛选
- 论文详情页展示
- 收藏、已读、忽略等状态管理
- 手动刷新与定时刷新

### 2.3 暂不纳入 MVP

- 全文 PDF 智能解析
- 多端同步
- 云端账号体系
- 向量检索
- 多人协作
- 复杂推荐系统

### 2.4 LLM 能力边界

当前设计文档不把 LLM 作为主链路依赖，但会预留扩展接口。第一版应用应在未配置任何 LLM 的情况下完整可用。

## 3. 技术栈说明

## 3.1 Python

选择 Python 的原因：

- 适合快速实现 arXiv API 接入、规则过滤、本地任务调度与后续 AI 集成
- 生态成熟，适合处理 HTTP、XML、SQLite、PDF、文本分析等任务
- 单语言即可覆盖 UI、业务逻辑、数据访问与集成层

适用职责：

- 应用入口与业务编排
- arXiv 数据同步
- 本地数据访问
- 规则引擎
- 后续第三方 LLM 适配

## 3.2 Flet

选择 Flet 的原因：

- 使用 Python 即可构建桌面 UI
- 对 MVP 级别的列表、详情、表单、筛选、导航等界面足够友好
- 开发与打包成本低于传统前后端分离方案
- 适合作为单机桌面应用的 UI 壳

适用职责：

- 主窗口
- 页面路由与导航
- 列表、详情、表单、筛选交互
- 通知与加载状态展示

约束与判断：

- Flet 适合作为当前阶段 UI 技术
- 若后续出现大量复杂桌面原生交互需求，可再评估迁移至 PySide6

## 3.3 sqlite3

选择 sqlite3 的原因：

- Python 标准库自带，无额外部署成本
- 适合本地单机持久化
- 事务、一致性、查询能力明显优于基于 JSON 的文档存储
- 便于做去重、排序、筛选、状态管理和同步审计

适用职责：

- 论文元数据存储
- 订阅规则持久化
- 用户状态持久化
- 同步任务与错误记录
- 配置存储

结论：

- sqlite3 作为唯一主存储
- 不引入 TinyDB 作为主库

## 3.4 httpx

选择 httpx 的原因：

- 同时支持同步与异步调用，适合本地桌面应用逐步演进
- API 简洁，便于封装统一的 arXiv 客户端
- 后续若加入第三方 LLM HTTP 接入，可复用同一套客户端能力

适用职责：

- 发起 arXiv API 请求
- 控制超时、重试、连接配置
- 后续 LLM API 调用

约束与判断：

- 上层不直接依赖 `httpx`
- 所有网络调用通过 `infrastructure/` 适配器层完成

## 3.5 feedparser

选择 feedparser 的原因：

- arXiv API 返回 Atom feed，`feedparser` 适合做第一层解析
- 可降低手写 XML 遍历逻辑的复杂度
- 能与后续标准化映射逻辑分层

适用职责：

- Atom/XML 基础解析
- 输出中间结构供 arXiv 标准化转换器消费

约束与判断：

- `feedparser` 只负责解析，不承担领域对象构建职责
- 领域模型映射在 `arxiv/parser.py` 内完成

## 3.6 pydantic

选择 pydantic 的原因：

- 比 `dataclass` 更适合当前项目的数据契约、DTO、输入校验和序列化边界
- 能统一外部输入、数据库读写、应用层 DTO 的校验方式
- 对未来配置建模、LLM 输出约束也更友好

适用职责：

- 领域模型定义
- DTO 定义
- 仓储层与应用层之间的数据校验
- 外部数据标准化后的结构校验

约束与判断：

- 项目中的主数据模型优先使用 `BaseModel`
- 若后续采用 Pydantic Settings 管理启动配置，则需增加 `pydantic-settings`

## 3.7 python-dateutil

选择 python-dateutil 的原因：

- 作为时间解析的稳妥兜底方案
- 可降低不同时间字符串格式带来的解析脆弱性

适用职责：

- 解析 arXiv 返回的时间字符串
- 解析未来其他来源的日期时间字段

约束与判断：

- 内存中统一使用 Python `datetime`
- 持久化前统一转为 UTC ISO 8601 字符串

## 3.8 python-dotenv

选择 python-dotenv 的原因：

- 适合管理本地开发环境下的冷启动配置
- 可以把数据库路径、调试开关、默认 API 入口等配置从代码中移出
- 与本地单机应用形态兼容

适用职责：

- 加载 `.env` 文件
- 为应用启动时的基础配置提供环境变量来源

约束与判断：

- `.env` 只用于冷启动配置
- 运行期用户设置仍持久化到 SQLite 的 `app_settings` 表中

## 4. 总体架构

## 4.1 架构原则

- 本地优先：核心能力不依赖后端服务
- 单进程优先：MVP 采用单进程应用，减少复杂度
- 分层清晰：UI、应用服务、领域模型、基础设施分离
- 可扩展：为后续 PDF、LLM、推荐能力预留接口
- 可恢复：同步失败不影响本地浏览与历史数据使用

## 4.2 逻辑分层

```text
+--------------------------------------------------+
|                   Flet UI Layer                  |
|  页面 / 组件 / 表单 / 列表 / 详情 / 通知 / 路由     |
+--------------------------------------------------+
|               Application Service Layer          |
|  订阅管理 / 同步编排 / 查询服务 / 状态更新 / 调度    |
+--------------------------------------------------+
|                  Domain Model Layer              |
|  Paper / Subscription / SyncRun / UserStatus     |
|  过滤规则 / 查询对象 / 数据契约                   |
+--------------------------------------------------+
|               Infrastructure Layer               |
|  arXiv Client / SQLite Repository / Scheduler    |
|  Config / Logging / Future LLM Adapter           |
+--------------------------------------------------+
```

## 4.3 模块划分建议

建议目录结构如下：

```text
app/
  main.py
  ui/
    app_shell.py
    pages/
      dashboard_page.py
      subscriptions_page.py
      paper_detail_page.py
      settings_page.py
    components/
  application/
    services/
      paper_query_service.py
      subscription_service.py
      sync_service.py
      status_service.py
    dto/
  domain/
    models/
    contracts/
    enums/
  infrastructure/
    db/
      connection.py
      migrations.py
      repositories/
    arxiv/
      client.py
      parser.py
    scheduler/
    config/
    logging/
    llm/
      base.py
  tests/
docs/
```

## 5. 核心模块职责

## 5.1 UI 层

职责：

- 展示订阅列表、论文列表、论文详情
- 接收用户输入
- 触发应用服务调用
- 展示任务状态、错误提示与刷新结果

要求：

- UI 不直接操作数据库
- UI 不直接发起外部网络请求

## 5.2 同步服务

职责：

- 根据订阅规则生成 arXiv 查询
- 执行增量同步
- 去重与更新论文版本
- 记录同步结果与错误信息

输入：

- `subscription_id`
- 可选时间范围
- 手动或定时触发上下文

输出：

- 同步结果摘要
- 新增数量
- 更新数量
- 错误信息

## 5.3 查询服务

职责：

- 聚合论文数据供列表与详情页展示
- 支持按主题、状态、日期、关键词过滤
- 返回稳定的 UI DTO

## 5.4 状态服务

职责：

- 设置收藏、已读、忽略等状态
- 管理本地备注
- 管理用户标签

## 5.5 arXiv 客户端

职责：

- 封装 arXiv API 请求
- 使用 `httpx` 发起网络调用
- 使用 `feedparser` 完成 Atom 第一层解析
- 输出标准化后的 Pydantic 模型

要求：

- 屏蔽上层对 arXiv 响应格式细节的感知
- 对时间、作者、分类、链接字段做统一解析
- 将解析与领域建模分离，避免 UI 和服务层感知 Atom 细节

## 5.6 SQLite 仓储层

职责：

- 提供模型级 CRUD 能力
- 封装 SQL 查询
- 隔离上层与底层表结构

## 6. 数据契约

## 6.1 设计原则

- 应用内部使用明确的 Pydantic 模型
- 存储结构与传输结构分离，但字段命名尽量一致
- 内存中统一使用 Python `datetime`
- SQLite 持久化时统一使用 UTC ISO 8601 字符串
- 外部输入与数据库出入边界通过 Pydantic 做校验与序列化
- 列表字段在 SQLite 中按 JSON 文本存储

## 6.2 关键领域对象

### 6.2.1 Paper

表示一篇 arXiv 论文的当前聚合视图。

```python
from datetime import datetime
from pydantic import BaseModel

class Paper(BaseModel):
    arxiv_id: str
    version: int
    title: str
    abstract: str
    authors: list[str]
    primary_category: str
    categories: list[str]
    published_at: datetime
    updated_at: datetime
    pdf_url: str | None
    abs_url: str
    comment: str | None
    journal_ref: str | None
    doi: str | None
```

字段约束：

- `arxiv_id`：不含版本号的主标识，例如 `2501.01234`
- `version`：当前已知最新版本号
- `primary_category`：主分类，例如 `cs.CV`
- `categories`：所有分类列表

### 6.2.2 Subscription

表示一个主题订阅规则。

```python
from pydantic import BaseModel

class Subscription(BaseModel):
    id: str
    name: str
    enabled: bool
    categories: list[str]
    include_keywords: list[str]
    exclude_keywords: list[str]
    authors: list[str]
    query_text: str | None
    sync_interval_minutes: int
```

约束说明：

- `name`：用户可读名称
- `categories`：arXiv 分类白名单
- `include_keywords`：标题与摘要匹配关键词
- `exclude_keywords`：排除关键词
- `query_text`：保留扩展字段，用于未来支持高级原始查询

### 6.2.3 PaperStatus

表示用户对某篇论文的本地状态。

```python
from datetime import datetime
from pydantic import BaseModel

class PaperStatus(BaseModel):
    arxiv_id: str
    is_starred: bool
    is_read: bool
    is_hidden: bool
    rating: int | None
    note: str | None
    tags: list[str]
    updated_at: datetime
```

### 6.2.4 SyncRun

表示一次同步执行记录。

```python
from datetime import datetime
from pydantic import BaseModel

class SyncRun(BaseModel):
    id: str
    subscription_id: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    fetched_count: int
    inserted_count: int
    updated_count: int
    error_message: str | None
```

## 6.3 SQLite 表设计

### 6.3.1 `papers`

存论文的当前快照。

| 字段 | 类型 | 说明 |
| :---: | :---: | :---: |
| arxiv_id | TEXT PRIMARY KEY | 论文主键 |
| latest_version | INTEGER NOT NULL | 最新版本号 |
| title | TEXT NOT NULL | 标题 |
| abstract | TEXT NOT NULL | 摘要 |
| authors_json | TEXT NOT NULL | 作者列表 JSON |
| primary_category | TEXT NOT NULL | 主分类 |
| categories_json | TEXT NOT NULL | 分类列表 JSON |
| published_at | TEXT NOT NULL | 首次发布时间 UTC |
| updated_at | TEXT NOT NULL | 最近更新时间 UTC |
| pdf_url | TEXT | PDF 地址 |
| abs_url | TEXT NOT NULL | 摘要页地址 |
| comment | TEXT | 评论字段 |
| journal_ref | TEXT | 期刊引用 |
| doi | TEXT | DOI |
| created_at | TEXT NOT NULL | 首次入库时间 |
| synced_at | TEXT NOT NULL | 最近同步时间 |

建议索引：

- `idx_papers_primary_category`
- `idx_papers_updated_at`
- `idx_papers_published_at`

### 6.3.2 `paper_versions`

存每篇论文的版本级记录，便于跟踪更新。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 主键 |
| arxiv_id | TEXT NOT NULL | 论文主键 |
| version | INTEGER NOT NULL | 版本号 |
| title | TEXT NOT NULL | 该版本标题快照 |
| abstract | TEXT NOT NULL | 该版本摘要快照 |
| updated_at | TEXT NOT NULL | 该版本更新时间 |
| raw_payload_json | TEXT | 原始标准化载荷 |

唯一约束：

- `(arxiv_id, version)`

### 6.3.3 `subscriptions`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PRIMARY KEY | 订阅主键 |
| name | TEXT NOT NULL | 订阅名称 |
| enabled | INTEGER NOT NULL | 是否启用 |
| categories_json | TEXT NOT NULL | 分类列表 JSON |
| include_keywords_json | TEXT NOT NULL | 包含关键词 JSON |
| exclude_keywords_json | TEXT NOT NULL | 排除关键词 JSON |
| authors_json | TEXT NOT NULL | 作者过滤 JSON |
| query_text | TEXT | 扩展高级查询 |
| sync_interval_minutes | INTEGER NOT NULL | 同步周期 |
| created_at | TEXT NOT NULL | 创建时间 |
| updated_at | TEXT NOT NULL | 更新时间 |
| last_synced_at | TEXT | 最近同步时间 |

### 6.3.4 `paper_statuses`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| arxiv_id | TEXT PRIMARY KEY | 论文主键 |
| is_starred | INTEGER NOT NULL DEFAULT 0 | 是否收藏 |
| is_read | INTEGER NOT NULL DEFAULT 0 | 是否已读 |
| is_hidden | INTEGER NOT NULL DEFAULT 0 | 是否忽略 |
| rating | INTEGER | 手动评分 |
| note | TEXT | 用户备注 |
| tags_json | TEXT NOT NULL | 标签列表 JSON |
| updated_at | TEXT NOT NULL | 更新时间 |

### 6.3.5 `sync_runs`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | TEXT PRIMARY KEY | 同步任务主键 |
| subscription_id | TEXT NOT NULL | 订阅主键 |
| trigger_type | TEXT NOT NULL | manual / scheduled |
| started_at | TEXT NOT NULL | 开始时间 |
| finished_at | TEXT | 结束时间 |
| status | TEXT NOT NULL | running / success / failed |
| fetched_count | INTEGER NOT NULL DEFAULT 0 | 抓取条数 |
| inserted_count | INTEGER NOT NULL DEFAULT 0 | 新增条数 |
| updated_count | INTEGER NOT NULL DEFAULT 0 | 更新条数 |
| error_message | TEXT | 错误信息 |

### 6.3.6 `app_settings`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| key | TEXT PRIMARY KEY | 配置键 |
| value_json | TEXT NOT NULL | 配置值 JSON |
| updated_at | TEXT NOT NULL | 更新时间 |

说明：

- 冷启动配置不进入此表，优先来自 `.env` 与系统环境变量
- 运行期用户配置进入此表
- 不再额外引入 TinyDB

建议配置分层：

- `.env` / 系统环境变量：数据库路径、调试模式、实验性开关、开发时默认 API 基础配置
- `app_settings`：刷新间隔、默认筛选、窗口状态、用户界面偏好

## 6.4 UI 查询 DTO

### 6.4.1 PaperListItemDTO

用于论文列表页。

```python
from datetime import datetime
from pydantic import BaseModel

class PaperListItemDTO(BaseModel):
    arxiv_id: str
    title: str
    authors_preview: str
    primary_category: str
    categories: list[str]
    published_at: datetime
    updated_at: datetime
    is_starred: bool
    is_read: bool
    is_hidden: bool
```

### 6.4.2 PaperDetailDTO

用于详情页。

```python
from datetime import datetime
from pydantic import BaseModel

class PaperDetailDTO(BaseModel):
    arxiv_id: str
    latest_version: int
    title: str
    abstract: str
    authors: list[str]
    primary_category: str
    categories: list[str]
    published_at: datetime
    updated_at: datetime
    pdf_url: str | None
    abs_url: str
    comment: str | None
    journal_ref: str | None
    doi: str | None
    is_starred: bool
    is_read: bool
    is_hidden: bool
    rating: int | None
    note: str | None
    tags: list[str]
```

说明：

- Repository 层返回原始记录或仓储对象
- Application Service 层负责组装并返回 DTO
- UI 层只消费 DTO，不直接处理 SQLite 原始行

## 7. 同步与处理流程

## 7.1 手动同步流程

```text
用户点击刷新
  -> UI 调用 SyncService
  -> 读取订阅规则
  -> 生成 arXiv 查询参数
  -> 使用 httpx 请求 arXiv API
  -> 使用 feedparser 解析 Atom
  -> 标准化并校验为 Pydantic 模型
  -> 与本地 papers / paper_versions 对比
  -> 执行插入或更新
  -> 记录 sync_runs
  -> UI 刷新列表
```

## 7.2 定时同步流程

```text
调度器触发
  -> 遍历 enabled subscription
  -> 按订阅逐个执行同步
  -> 写入 sync_runs
  -> 可选发出 UI 通知
```

## 7.3 过滤规则处理顺序

建议过滤顺序：

1. 分类初筛
2. 标题关键词匹配
3. 摘要关键词匹配
4. 排除关键词过滤
5. 作者过滤

这样做的原因：

- 分类判断开销最低
- 规则顺序更容易解释
- 调试订阅结果更直观

## 8. 错误处理与日志

## 8.1 错误分类

- 网络错误
- arXiv 响应解析错误
- 本地数据库写入错误
- 规则配置错误
- UI 输入校验错误

## 8.2 错误处理原则

- 同步失败不应导致应用崩溃
- 单订阅失败不应阻塞其他订阅
- 用户可在 UI 中看到最近一次失败原因
- 原始异常应写入日志

## 8.3 日志建议

至少记录：

- 同步开始/结束
- 查询参数
- 返回条数
- 入库条数
- 更新条数
- 异常堆栈

## 9. 非功能性要求

## 9.1 性能

- 常规论文列表查询应在本地快速返回
- 单次同步应支持几十到几百条论文的稳定入库
- UI 主线程不得阻塞网络与数据库长任务

## 9.2 可维护性

- SQL 与业务逻辑分层
- DTO 与表结构分离
- 所有外部依赖通过适配器封装

## 9.3 可扩展性

后续可扩展方向：

- PDF 下载与全文缓存
- 第三方 LLM 分析
- 本地全文索引
- 多来源论文聚合
- 导出 Markdown / CSV / BibTeX

## 9.4 可测试性

建议优先覆盖：

- 订阅规则匹配逻辑
- arXiv 响应解析
- 数据库存储与去重逻辑
- 同步流程编排

## 10. 面向未来的 LLM 预留设计

尽管 LLM 不进入 MVP 主链路，建议预留标准接口：

```python
from typing import Protocol

class LLMAdapter(Protocol):
    def summarize_paper(self, paper: "Paper") -> str:
        ...
```

后续扩展时：

- UI 只调用应用服务
- 应用服务只依赖 `LLMAdapter`
- 具体 OpenAI-compatible / 其他供应商适配器放在 `infrastructure/llm/`
- 若后续输出结构复杂化，应引入 Pydantic 总结结果模型而非返回裸字符串

## 11. 初步迭代计划

## 11.1 第一阶段

- 建立项目骨架
- 初始化 sqlite3 schema
- 完成订阅管理页
- 完成 arXiv 基础同步链路

## 11.2 第二阶段

- 完成论文列表页
- 完成详情页
- 完成收藏、已读、忽略状态管理

## 11.3 第三阶段

- 完成定时同步
- 完成日志与错误展示
- 补充测试与打包流程

## 12. 当前决策摘要

- 主技术栈确定为 `Python + Flet + sqlite3`
- 基础依赖假设为 `httpx + feedparser + pydantic + python-dateutil + python-dotenv`
- sqlite3 作为唯一主持久化存储
- 应用采用本地优先、单进程、分层架构
- LLM 不作为 MVP 依赖，但保留扩展接口
- 数据契约优先围绕论文、订阅、用户状态、同步记录四类对象设计
- 项目主数据模型优先采用 Pydantic，而非 `dataclass`
- 冷启动配置与运行期配置分层管理
