# 实现计划总览

本文档汇总当前 MVP 第一条主链路的四个实现 slice。目标不是一次性铺满所有模块，而是按依赖顺序打通：

`领域模型 -> 存储 -> arXiv 接入 -> 同步编排`

## Slice 列表

1. [01-domain-and-dto.md](C:\Users\Vermosh\Desktop\Project\paper_research\docs\plans\01-domain-and-dto.md)
2. [02-sqlite-and-repository.md](C:\Users\Vermosh\Desktop\Project\paper_research\docs\plans\02-sqlite-and-repository.md)
3. [03-arxiv-client-and-parser.md](C:\Users\Vermosh\Desktop\Project\paper_research\docs\plans\03-arxiv-client-and-parser.md)
4. [04-sync-service.md](C:\Users\Vermosh\Desktop\Project\paper_research\docs\plans\04-sync-service.md)

## 推荐执行顺序

1. 先完成 `domain + dto`
2. 再完成 `sqlite + repository`
3. 然后完成 `arxiv client + parser`
4. 最后串起 `sync service`

## 原因

- `domain + dto` 决定数据边界
- `repository` 决定本地持久化模型和查询接口
- `arxiv client + parser` 决定外部数据进入系统的方式
- `sync service` 负责把前面三层编排成第一条真实业务链路

## 交付目标

四个 slice 完成后，应至少具备以下能力：

- 能定义并校验订阅、论文、用户状态、同步记录等主模型
- 能初始化 SQLite schema，并完成基础 CRUD 与查询
- 能从 arXiv API 拉取数据并标准化为内部模型
- 能按订阅执行一次完整同步并记录结果

## 非目标

当前这组计划不包含：

- Flet 页面实现
- PDF 下载与全文解析
- LLM 分析接入
- 定时调度
- 打包发布

