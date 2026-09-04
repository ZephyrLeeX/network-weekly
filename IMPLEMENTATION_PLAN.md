# IMPLEMENTATION_PLAN.md

## Purpose

本计划描述 Network Weekly Report System 从空仓库到可交付版本的最短实现路径。

实施原则：

> 先建立最小工程骨架，尽早验证真实 H3C 采集，然后完成稳定监控、周统计和 DOCX；业务闭环通过后再补登录、下载页面和部署脚本。

Live Task 状态只记录在：

```text
TASK_GRAPH.md
EXECUTION_STATE.md
```

---

# Wave 0 — Engineering Foundation

## Objective

建立最小、可重复、可测试的 Python/PostgreSQL/Docker 基础。

## Outputs

```text
application package
pyproject.toml
uv.lock
pytest
ruff
type-check command
Dockerfile
docker-compose.yml
PostgreSQL
SQLAlchemy / Alembic
runtime configuration
secret-safe logging
health endpoint
worker heartbeat
```

Compose：

```text
web
worker
postgres
```

## Exit Gate

```text
web starts
worker starts
postgres starts
alembic upgrade head succeeds
health succeeds
worker heartbeat visible
tests/lint/type commands succeed
runtime dependency set matches SYSTEM_SPEC.md
```

---

# Wave 1 — Real H3C Collection

## Objective

证明 S10500X / S12500 的真实 SNMP 和受控 SSH 采集路径。

## Outputs

```text
devices.toml loader
0600 secrets loader
Device / DeviceMember / Interface schema
aggregation relationship schema
SNMP transport
limited read-only SSH transport
H3C normalized DTOs
identity/version normalization
CPU/memory collection
interface discovery
aggregation/member discovery
IRF member discovery
CRC/error/drop counter discovery
SUCCESS/PARTIAL/FAILED collection result
real-device anonymized fixtures
```

## Validation order

1. 一台独立 S10500X。
2. 一组 S10500X IRF。
3. 一组 S12500 IRF。
4. 固化 DTO 与 parser fixture。
5. 扩展到当前全部设备。

## Critical Gate

真实设备验证必须证明：

```text
SNMP reachable
SSH lightweight read/check works
identity/model/version normalized
CPU/memory available
interfaces discovered
aggregation relationship available
IRF members recognized
required counters identified where supported
valid results persisted
partial result preserved
secrets absent from logs and fixtures
```

未通过真实设备 Gate 不进入正式监控流水线。

---

# Wave 2 — Monitoring Pipeline

## Objective

形成稳定的 5 分钟采集、指标处理和最小故障状态语义。

## Outputs

```text
device_poll_runs
device_metrics
interface_metrics
5-minute DEVICE_POLL scheduler
actual-elapsed utilization
counter reset / rebaseline
device reachability state machine
priority interface configuration service
priority interface state machine
CPU/memory sustained-high detection
priority interface high-utilization detection
IRF periodic observation
Monitoring Coverage source data
90-day retention maintenance
```

## Required rules

### Device

```text
SNMP fail -> one lightweight SSH confirmation
2 consecutive SNMP+SSH failed cycles -> DOWN
2 consecutive management-reachable cycles -> RECOVERED
```

### Priority interface

```text
2 valid DOWN cycles -> DOWN
2 valid UP cycles -> RECOVERED
missing samples do not count as UP or DOWN
```

### Thresholds

```text
CPU >= 80% for 15 min
Memory >= 80% for 15 min
Priority interface utilization >= 80% for 15 min
```

## Exit Gate

- 5 分钟调度不会重复执行同一设备并发采集。
- 单设备失败不阻塞其他设备。
- PARTIAL 正确保留有效数据。
- Counter Reset 不产生假流量峰值。
- Device / Interface 2-cycle 状态测试通过。
- IRF 成员状态可持久化。
- Coverage 的 expected/success/partial/failed 来源可信。
- 90 天清理测试通过。

---

# Wave 3 — Weekly Statistics and DOCX

## Objective

完成核心业务闭环：

```text
persisted monitoring data
-> weekly statistics
-> deterministic summary
-> DOCX
-> Monday automatic generation
-> retry/recovery
```

## Outputs

```text
ReportPeriodService
ISO week code
WeeklyStatisticsService
CPU average/max/P95
Memory average/max/P95
sustained-high intervals
interface Top 10 P95 utilization
device weekly incident summary
priority-interface weekly incident summary
IRF weekly summary
CRC/Error/Drop weekly deltas + Top 10
Monitoring Coverage
overall status
weekly_reports
report_jobs
python-docx renderer
atomic file replace
Monday 00:10 scheduling
10-minute retry
manual regenerate service
```

## DOCX sections

```text
1 周报基本信息与总体摘要
2 设备运行状态与掉线/恢复情况
3 IRF 堆叠成员状态
4 CPU / 内存统计
5 重点接口状态与 Down/恢复情况
6 接口利用率 Top 10 与高利用率异常
7 CRC / Error / Drop 与 Monitoring Coverage
8 本周处理问题
```

## Golden cases

必须固定测试：

- `[Monday 00:00, next Monday 00:00)` 边界。
- ISO year boundary。
- 跨周设备 Down。
- 周内 Down + Recovery。
- 重点接口 Down + Recovery。
- Missing sample 中断 2-cycle / 15-minute 连续性。
- CPU/Memory P95。
- Interface Top 10 P95 算法。
- Counter Reset。
- CRC/Error/Drop reset。
- PARTIAL Poll。
- Coverage <95%。
- 整周设备无数据。
- 同周 regenerate。

## Business Gate

至少人工核对一份真实周数据生成的 DOCX：

```text
period correct
values plausible
tables readable
8 sections complete
missing data explicit
no secret leakage
blank "本周处理问题" area editable
```

并验证：

- 周一 00:10 自动任务。
- 生成失败后 10 分钟重试。
- Worker 重启后任务恢复。
- 同一周最多一个活跃报告任务。

---

# Wave 4 — Login and Operations Web

## Objective

提供日常使用所需的最小 Web 操作入口。

## Outputs

```text
single administrator
salted scrypt password hash
server-side session
login/logout
CSRF protection
report list
report status
DOCX download
manual regenerate form
priority-interface configuration page
aggregation/member display
```

## Web behavior

Report list：

```text
week
period
generated_at
status
last_error
download
regenerate
```

Priority interface page：

```text
select device
interface name
description
current state
aggregation/member relationship
monitored toggle
```

## Exit Gate

- 未登录请求被拒绝。
- 登录/退出正常。
- Session 7-day absolute + 12-hour idle 生效。
- CSRF 防护覆盖状态修改操作。
- 报告可查看和下载。
- regenerate 不产生重复并发任务。
- 重点接口可配置。
- 聚合接口切换不会自动切换成员接口。
- 密码和设备 Secret 不出现在页面或日志。

---

# Wave 5 — Deployment and Stability

## Objective

让系统在目标 Debian 13 隔离环境中可安装、可更新并长期稳定运行。

## Outputs

```text
production compose
/opt /data /etc directory layout
install.sh
update.sh
health verification
worker heartbeat verification
retention scheduling
bounded logs
operator README
weekly report runbook
```

## Stability acceptance

至少完成：

1. Debian 13 amd64 目标环境安装通过。
2. 无 Internet 运行通过。
3. 当前 10 个逻辑设备采集验证完成。
4. 独立 S10500X 验证完成。
5. S10500X IRF 验证完成。
6. S12500 IRF 验证完成。
7. 聚合接口和重点接口配置验证完成。
8. 连续运行至少两个完整报告周。
9. 连续两个周一自动生成报告。
10. 注入 DOCX 生成失败，验证 10 分钟重试恢复。
11. 报告时间前重启 Worker，验证任务恢复。
12. regenerate 已有周，验证只保留一个当前服务器 DOCX。
13. seeded 数据验证 90 天清理。
14. `update.sh` migration/restart/health/heartbeat 流程通过。

## Release Gate

```text
all Wave Gates PASS
real-device collection PASS
monitoring state semantics PASS
weekly statistics golden tests PASS
DOCX render verification PASS
Monday scheduling PASS
retry/restart recovery PASS
login/download/priority-interface workflow PASS
install/update PASS
no known P0/P1 defect affecting weekly report correctness or reliability
```

---

# Development priority

```text
P0 report correctness / report cannot be generated
P1 collection correctness / data loss / silent scheduler or retry failure
P2 operational login/download/priority-interface usability
P3 layout and convenience
```

P2/P3 工作不得延误 P0/P1。
