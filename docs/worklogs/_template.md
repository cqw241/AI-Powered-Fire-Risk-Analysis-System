# Worklog：<feature-slug>

> 本模板用于复杂功能或需要交接时的可选过程记录。复制为 `docs/worklogs/<feature-slug>.md`；不要求每个分支都创建 worklog。

## 1. 分支信息

| 项目 | 内容 |
| --- | --- |
| Branch | `<feature/Fxx-short-description>` |
| Feature | `<Fxx>` |
| 负责人 | `<name>` |
| 创建日期 | `<YYYY-MM-DD>` |
| 当前状态 | `planned / in_progress / review / merged / blocked` |
| 前置分支/Feature | `<main / Fxx>` |

## 2. 目标与范围

### 目标

<!-- 用可验收的结果描述本分支要完成什么。 -->

### 范围内

-

### 明确不做

-

## 3. 设计依据与影响面

- 设计文档章节：
- 相关代码：
- 相关 Schema / Prompt：
- 相关法规数据：
- 依赖的前置 Feature：

## 4. Task 清单

| Task | 描述 | 状态 | 验收方式 |
| --- | --- | --- | --- |
| `<Fxx-T01>` |  | `todo / doing / done / blocked` |  |

## 5. Commit 记录

如使用本表，可按实际开发节奏更新；`Cxx` 是可选的记录序号，不要求一个 task 对应一个 commit，也不要求预填 Git SHA。

| Commit | Task | Commit message | 变更文件 | 验证命令与结果 | 日期 |
| --- | --- | --- | --- | --- | --- |
| `C01` | `<Fxx-T01>` |  |  |  |  |

## 6. 决策、风险与阻塞

| 日期 | 类型 | 记录 | 处理/结论 |
| --- | --- | --- | --- |
|  | `decision / risk / blocker` |  |  |

## 7. PR 与合并

| 项目 | 内容 |
| --- | --- |
| PR | `#<number>` |
| PR 标题 |  |
| Reviewer |  |
| CI 结果 |  |
| Review 结论 | `pending / approved / changes_requested` |
| 合并方式 | `rebase / merge / squash（需说明）` |
| 合并日期 |  |
| 后续 issue |  |

## 8. 最终总结

### 已完成

-

### 未完成/已知限制

-

### 交接说明

-
