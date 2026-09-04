# AGENTS.md

## Project

Network Weekly Report System / 网络运维自动周报系统

本仓库唯一业务目标：**稳定采集一个隔离网络内指定 H3C 核心交换机的运行数据，并在每周一自动生成上一完整自然周的 DOCX 网络运维周报。**

稳定生成正确周报的优先级高于功能数量、界面丰富度和架构扩展性。

## Authoritative project controls

开始任何实现前，按以下顺序读取：

1. `EXECUTION_STATE.md`
2. `TASK_GRAPH.md`
3. `SYSTEM_SPEC.md`
4. `IMPLEMENTATION_PLAN.md`
5. 与当前任务直接相关的代码和测试

权威来源：

- 产品与架构：`SYSTEM_SPEC.md`
- 任务状态：`TASK_GRAPH.md` + `EXECUTION_STATE.md`
- 开发路线：`IMPLEMENTATION_PLAN.md`
- Agent 执行规则：`AGENTS.md`

仓库中没有其他产品规格或任务状态权威来源。

## Scope rule

Codex 只能实现 `SYSTEM_SPEC.md` 明确要求的能力，以及当前 `READY` Task 为完成这些要求所必需的最小实现。

任何未写入 `SYSTEM_SPEC.md` 的产品能力、基础设施、抽象层、通用框架或扩展机制，都不得自行加入。

不要以“以后可能需要”为理由进行 speculative future-proofing。

## Current product boundary

当前系统包含：

- 一个隔离网络实例。
- H3C S10500X / S12500。
- 当前规模 10 个逻辑设备、12 个物理机框。
- SNMP 周期采集。
- 受控的少量 SSH 读取。
- 5 分钟设备与接口采集。
- IRF 成员状态采集。
- 聚合接口与成员关系识别。
- 重点接口人工选择。
- CPU / 内存 / 接口利用率统计。
- 设备 Down / Recovery 记录。
- 重点接口 Down / Recovery 记录。
- CRC / Error / Drop 观察统计。
- Monitoring Coverage。
- 每周一 00:10 自动生成 DOCX。
- 报告失败自动重试。
- 单管理员登录。
- 历史报告查看与下载。
- 手工重新生成报告。
- 重点接口配置页面。
- Debian 13 amd64 + Docker Engine + Docker Compose + PostgreSQL。
- `install.sh` 与 `update.sh`。

## Engineering rules

Codex 必须：

1. 只处理 `READY` Task。
2. 开始 Task 前读取其 Scope、Acceptance、依赖和相关 `SYSTEM_SPEC.md` 章节。
3. 保持改动最小，只完成当前 Task。
4. 所有数据库结构变化使用 Alembic。
5. 真实 H3C 采集能力必须尽早验证，不能仅依赖模拟数据宣称采集完成。
6. Transport / Adapter 只负责采集、解析、标准化；业务统计由独立服务完成。
7. 不把采集失败直接等同于设备故障。
8. 周报统计只读取已经持久化的数据；生成报告时不得临时访问交换机补数据。
9. 同一统计周的报告生成必须幂等，并且同一时间最多存在一个生成任务。
10. 报告生成失败必须有可见状态、错误摘要和自动重试。
11. 单设备或单接口采集异常不得阻塞其他设备数据入库。
12. Secret 不进入 Git、日志、HTML、DOCX 或数据库明文列。
13. 完成 Task 前运行其要求的测试。
14. Task 只有通过自审和 Acceptance 后才能标记 `REVIEW_PASSED`。
15. 每个 `REVIEW_PASSED` Task 必须有明确 checkpoint commit SHA。
16. Task 状态变化后同步更新 `TASK_GRAPH.md` 与 `EXECUTION_STATE.md`。

## Git strategy

使用：

```text
main
work/wave-00
work/wave-01
work/wave-02
work/wave-03
work/wave-04
work/wave-05
```

一个 Wave 在对应 `work/wave-XX` 分支开发。

Wave Gate 通过后再合并到 `main`。

## Security minimum

- 系统仅部署在隔离内网。
- Web 页面必须登录后访问。
- 系统只有一个本地管理员账号。
- 管理员密码使用 salted scrypt 哈希保存，不保存明文。
- Session 服务端持久化。
- Session 绝对有效期 7 天，空闲有效期 12 小时。
- Session Cookie 使用 `HttpOnly`、`SameSite=Lax`。
- 设备 SNMP / SSH Secret 保存在宿主机独立 secrets 文件中。
- secrets 文件权限必须为 `0600`。
- secrets 文件不得进入镜像或 Git。
- 日志必须过滤密码、community、私钥和完整认证字符串。
- HTTP 管理页面只能在可信隔离内网中使用。

## Real-device evidence rule

涉及设备采集或解析的 Task，最终 `REVIEW_PASSED` 必须包含真实设备证据。

最低验证集合：

- 1 台独立 S10500X。
- 1 组 S10500X IRF。
- 1 组 S12500 IRF。

真实设备输出进入 fixture 前必须去除：

- 管理 IP。
- hostname 中的敏感信息。
- SNMP community。
- 用户名/密码。
- 其他认证 Secret。

## Definition of Done

Task 不是“代码写完”即完成。至少满足：

```text
implementation complete
+ acceptance criteria pass
+ targeted tests pass
+ required integration tests pass
+ Alembic migration exists when schema changes
+ error handling implemented
+ secret-safety reviewed
+ related documentation updated
+ no unrequested product capability introduced
+ checkpoint commit recorded after REVIEW_PASSED
```
