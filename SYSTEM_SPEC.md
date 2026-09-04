# SYSTEM_SPEC.md

## 1. Authority and goal

本文件是 Network Weekly Report System / 网络运维自动周报系统的唯一产品与架构需求权威来源。

系统唯一核心目标：

> 对一个隔离网络中的指定 H3C 核心交换机进行可靠周期采集，并在每周一稳定生成上一完整自然周的 DOCX 网络运维周报，供运维人员下载后手工补充“本周处理问题”并发送。

设计优先级：

```text
1. 周报数据正确
2. 周一能够稳定生成报告
3. 采集失败可诊断、可恢复
4. 日常操作简单
5. 依赖和维护成本尽量低
```

任何产品能力只有在本文件明确规定后才属于实现范围。

---

## 2. Deployment scope

### 2.1 Network

一个系统实例管理一个隔离网络。

当前只部署一个实例，不设计跨网络聚合、网络租户或多网络管理模型。

### 2.2 Managed devices

当前设备拓扑：

- 8 台独立 H3C S10500X。
- 2 台 H3C S10500X 组成 1 个 IRF。
- 2 台 H3C S12500 组成 1 个 IRF。

当前规模：

```text
10 logical devices
12 physical chassis / IRF members
```

设备模型：

- `Device`：一个管理 IP 对应的逻辑设备。
- `DeviceMember`：IRF 中的物理成员。

独立设备不要求为了模型形式额外创建成员记录；实现只需保证 IRF 设备成员状态能够稳定表达。

只支持已经在真实环境验证的 S10500X / S12500 采集路径。

---

## 3. Time model

业务时区固定为：

```text
Asia/Shanghai
UTC+8
```

所有数据库业务时间使用带时区时间类型。

周报统计周期采用半开区间：

```text
[start, end)
```

其中：

```text
start = 上周一 00:00:00 Asia/Shanghai
end   = 本周一 00:00:00 Asia/Shanghai
```

用户界面和 DOCX 可以展示为“周一至周日”。

报告周编号使用 ISO week-year，例如：

```text
2026-W36
```

---

## 4. Automatic weekly report

### 4.1 Schedule

每周一：

```text
00:10 Asia/Shanghai
```

自动生成刚结束完整自然周的 DOCX。

### 4.2 Persistent responsibility

报告调度责任必须持久化到 PostgreSQL。

Worker 重启后必须能够恢复：

- 尚未执行的本周报告任务。
- 生成失败等待重试的报告任务。

不得把周报生成责任只保存在内存定时器中。

### 4.3 Failure retry

生成失败时：

- 保存失败状态。
- 保存可读错误摘要。
- 每 10 分钟重试一次。
- 成功后停止重试。
- 同一统计周最多允许一个活跃生成任务。

报告生成失败不得静默。

### 4.4 Manual regenerate

登录后的 Web 页面提供“重新生成”操作。

同一统计周在服务器端只维护一个当前 DOCX：

- 重新生成成功后原子替换该周 DOCX。
- 生成过程中不能破坏已有可下载文件。
- 用户下载后手工修改的文件不上传回系统。

---

## 5. DOCX output

唯一正式输出格式：

```text
DOCX
```

使用 `python-docx` 生成。

报告以文字和表格为主，不要求图表。

DOCX 文件按统计周长期保存。

建议文件名：

```text
network-weekly-report-2026-W36.docx
```

DOCX 写入流程必须使用：

```text
temporary file -> successful close/validation -> atomic replace
```

避免生成失败留下半文件。

---

## 6. Report structure

DOCX 固定包含以下 8 部分：

1. **周报基本信息与总体摘要**
2. **设备运行状态与掉线/恢复情况**
3. **IRF 堆叠成员状态**
4. **CPU / 内存统计**
5. **重点接口状态与 Down/恢复情况**
6. **接口利用率 Top 10 与高利用率异常**
7. **CRC / Error / Drop 与 Monitoring Coverage**
8. **本周处理问题**

第 8 部分由系统生成清晰空白区域，用户下载后在 Word/WPS 中手工填写。

### 6.1 Basic information

至少包含：

- 报告周编号。
- 统计开始时间。
- 统计结束时间。
- 报告生成时间。
- 当前总体状态。
- Monitoring Coverage 摘要。

### 6.2 Missing data presentation

某项没有有效数据时必须明确展示：

```text
数据缺失
```

不得把缺失数据自动替换为 0、正常或上一采样值。

---

## 7. Collection architecture

### 7.1 DEVICE_POLL

周期：

```text
5 minutes
```

SNMP 是周期指标采集主通道。

每个周期至少尝试采集：

- 设备基本可达状态。
- CPU。
- 内存。
- 接口 admin / oper 状态。
- 接口速率元数据。
- 接口输入/输出累计计数器。
- CRC / Error / Drop 累计计数器。
- 聚合接口和成员关系所需数据。

SNMP 采集应优先使用 bulk/walk 等批量方式，避免逐接口高频单项查询。

### 7.2 SSH usage

SSH 只用于受控读取：

- 设备身份。
- 软件版本。
- IRF 成员与角色信息。
- SNMP 无法可靠获得的少量静态信息。
- SNMP 失败时的一次轻量管理面连通性确认。

SSH 不作为 5 分钟高频指标主采集通道。

允许执行的命令必须在代码中明确列出，且全部为只读命令。

### 7.3 IRF observation

IRF 成员状态建议每 15 分钟刷新一次。

可以使用经过真实设备验证后更稳定、负载更低的 SNMP 或受控 SSH 路径。

### 7.4 Adapter boundary

Transport / Adapter 负责：

```text
collect
parse
normalize
```

输出稳定 DTO。

Transport / Adapter 不负责：

- 生成周报。
- 计算周统计结论。
- 决定总体状态。
- 修改设备配置。

---

## 8. Poll result model

每台逻辑设备每个计划周期必须形成一个 `device_poll_run`。

状态至少包含：

```text
SUCCESS
PARTIAL
FAILED
```

语义：

- `SUCCESS`：本周期报告所需核心采集部分成功。
- `PARTIAL`：部分有效数据已获得，但至少一个采集 section 失败。
- `FAILED`：无法获得可用于本周期核心统计的数据。

单个 section 失败时，已经成功获得的数据必须保留。

一台设备采集失败不得阻塞其他设备采集。

---

## 9. Device reachability

### 9.1 Failed cycle

每个 5 分钟周期：

1. 执行 SNMP 采集。
2. SNMP 成功：管理面可达。
3. SNMP 失败：执行一次轻量 SSH 连通性确认。
4. SSH 成功：设备仍视为管理面可达，但本周期存在 SNMP 采集异常。
5. SNMP 与 SSH 都失败：记为一个 reachability failed cycle。

### 9.2 Down confirmation

连续：

```text
2 个 5 分钟周期
```

SNMP 与 SSH 都失败，确认设备 `DOWN`。

### 9.3 Recovery confirmation

设备处于 `DOWN` 后，连续 2 个周期至少一种受支持管理通道可达，确认 `RECOVERED`。

如果 SSH 恢复但 SNMP 仍失败：

- 设备可以结束 Down 状态。
- SNMP 数据缺失继续反映在 Poll Result 和 Monitoring Coverage 中。

### 9.4 Persisted incident

确认后的设备 Down 记录至少保存：

- `device_id`。
- `started_at`。
- `recovered_at`，未恢复时为空。
- 当前状态。

周报基于这些持久化记录统计：

- 本周 Down 次数。
- 开始时间。
- 恢复时间。
- 持续时间。
- 跨周仍未恢复状态。

---

## 10. Interface discovery

系统自动发现接口，并持续更新接口元数据。

接口业务身份不能只依赖 `ifIndex`。

至少使用：

```text
device_id + normalized_interface_name
```

作为稳定业务身份；`ifIndex` 是可更新元数据。

接口至少保存：

- normalized name。
- display name。
- description。
- ifIndex。
- admin state。
- oper state。
- speed。
- aggregation flag。
- monitored flag。
- last seen time。

---

## 11. Aggregation interfaces

聚合逻辑接口作为独立 Interface。

系统保存：

```text
aggregation interface -> physical member interfaces
```

关系。

重点接口配置页面必须显示聚合关系。

当用户只勾选聚合接口时：

- 只把聚合逻辑接口作为重点接口。
- 不自动把成员物理口设置为重点接口。

成员物理口可以单独勾选。

---

## 12. Priority interface configuration

接口被自动发现后，默认：

```text
monitored = false
```

管理员通过 Web 页面手工选择重点接口。

页面至少提供：

- 设备选择。
- 接口名称。
- 接口描述。
- 当前 admin / oper 状态。
- 是否为聚合接口。
- 聚合成员关系。
- `monitored` 开关。

重点接口配置写入 PostgreSQL。

接口重新发现或 ifIndex 变化不得丢失正确的 `monitored` 状态。

---

## 13. Priority interface state

只有 `monitored = true` 的接口参与重点 Down / Recovery 周报统计。

### 13.1 Down

连续 2 个有效采样周期 oper state 为 Down，确认接口 `DOWN`。

### 13.2 Recovery

处于 Down 后连续 2 个有效采样周期 oper state 为 Up，确认 `RECOVERED`。

### 13.3 Missing samples

缺失、失败或无法确定的采样不得作为 Up 或 Down 采样参与连续次数判断。

不足两个有效周期的瞬时状态变化不进入正式周报 Down 记录。

---

## 14. CPU and memory statistics

默认阈值：

```text
CPU >= 80% for 15 minutes
Memory >= 80% for 15 minutes
```

阈值必须可配置。

5 分钟周期下，15 分钟要求至少连续 3 个有效样本达到阈值。

缺失样本会中断连续区间，不得人为补齐。

每个逻辑设备周报至少计算：

- Average。
- Maximum。
- P95。
- 持续高负载区间数量。
- 每个区间开始时间、结束时间和持续时间。

P95 使用 PostgreSQL：

```sql
percentile_cont(0.95)
```

---

## 15. Interface utilization

### 15.1 Calculation

接口利用率使用：

- 累计字节计数器 Delta。
- 两个有效样本的实际时间差。
- 接口有效速率。

分别计算 ingress 和 egress。

### 15.2 Counter reset

遇到：

- Counter reset。
- Counter wrap 无法可靠处理。
- Delta 为负。
- 间隔非法。
- 接口速率无效。

当前样本必须重新基线，不得生成虚假利用率尖峰。

### 15.3 High utilization

重点接口默认阈值：

```text
>= 80% for 15 minutes
```

即至少连续 3 个有效 5 分钟样本。

### 15.4 Top 10

周报输出接口利用率 Top 10。

候选接口为具有有效速率和有效周样本的接口，不要求必须标记为重点接口。

统一排序口径：

1. 每个有效样本取 `max(ingress_utilization, egress_utilization)`。
2. 对接口这一周的该值计算 P95。
3. 按 P95 从高到低取 Top 10。

所有页面和报告使用同一统计实现。

---

## 16. CRC / Error / Drop observation

采集设备能够稳定提供的接口累计错误计数器，例如：

- CRC。
- input errors。
- output errors。
- input drops/discards。
- output drops/discards。

周报计算统计周内增量。

Counter reset 时重新基线，不得产生虚假巨大增量。

周报至少展示增量最高的接口 Top 10。

这些数据只作为观察信息，不自动改变总体状态。

---

## 17. IRF monitoring

现网包含：

- 1 组 2-member S10500X IRF。
- 1 组 2-member S12500 IRF。

每组 IRF 配置 `expected_member_count`。

系统持久化成员观察结果。

周报至少展示：

- 期望成员数。
- 当前成员数。
- 本周观察到的成员。
- 成员缺失时间段。
- 成员重新出现时间。
- 能够稳定识别时的角色变化。

即使逻辑管理 IP 一直可达，只要 IRF 成员缺失，也必须独立展示。

---

## 18. Monitoring Coverage

Monitoring Coverage 表示采集数据完整程度。

### 18.1 Expected cycles

对每个逻辑设备：

```text
expected = 统计周期内计划的 5 分钟 DEVICE_POLL 次数
```

完整 7 天正常情况下为：

```text
7 * 24 * 12 = 2016 cycles
```

### 18.2 Counts

周报至少展示：

- expected。
- success。
- partial。
- failed。

Coverage 定义：

```text
coverage = (SUCCESS + PARTIAL) / expected * 100%
```

同时单独展示 PARTIAL 数量，因此百分比不会隐藏部分采集失败。

### 18.3 Warning

如果单设备或总体 Coverage：

```text
< 95%
```

周报明确显示：

```text
数据完整性不足
```

Coverage 低不能阻止报告生成。

---

## 19. Overall status

总体状态只有：

```text
正常
关注
异常
```

规则固定：

### 异常

统计期结束时存在任一：

- 设备仍处于确认 Down。
- 重点接口仍处于确认 Down。
- IRF 成员仍缺失。

### 关注

不存在“异常”条件，但本周存在任一：

- 已恢复的设备 Down。
- 已恢复的重点接口 Down。
- CPU 持续高负载。
- 内存持续高负载。
- 重点接口持续高利用率。
- Monitoring Coverage <95%。

### 正常

没有上述条件。

CRC / Error / Drop 观察值不改变总体状态。

总体摘要使用确定性文字模板生成，不依赖外部服务。

---

## 20. Web interface

Web 只提供日常操作必须页面。

### 20.1 Login

- 单管理员账号。
- 登录。
- 退出。

### 20.2 Report list

默认按周倒序显示：

- week。
- period。
- generated_at。
- status。
- last_error，存在时显示。
- download。
- regenerate。

### 20.3 Priority interfaces

提供第 12 节定义的重点接口配置页面。

### 20.4 Rendering approach

FastAPI 直接提供服务端 HTML 页面和普通表单。

不建立独立前端构建工程。

状态修改请求必须有 CSRF 防护。

---

## 21. Authentication

系统只有一个本地管理员账号。

管理员密码：

- 不保存明文。
- 使用 salted scrypt 哈希。

Session：

- 服务端持久化。
- 7 天绝对过期。
- 12 小时空闲过期。
- Cookie 使用 `HttpOnly`。
- Cookie 使用 `SameSite=Lax`。

未登录用户不能：

- 查看报告列表。
- 下载报告。
- 重新生成报告。
- 查看或修改重点接口配置。

---

## 22. Device inventory and secrets

### 22.1 Inventory

设备清单使用：

```text
/etc/network-report/devices.toml
```

只保存非敏感信息，例如：

- logical name。
- management IP。
- model family。
- expected IRF member count。
- credential profile reference。

应用提供显式 inventory sync 命令，将设备元数据同步到 PostgreSQL。

### 22.2 Secrets

设备 Secret 使用：

```text
/etc/network-report/secrets.env
```

权限：

```text
0600
```

文件所有者为 root 或部署服务账号。

Secret 不写入：

- Git。
- 镜像。
- `devices.toml`。
- HTML。
- DOCX。
- 日志。
- 数据库明文列。

应用启动时必须检查 secrets 文件权限；权限过宽时拒绝加载 Secret 并给出明确错误。

---

## 23. Data model

实现保持最小必要模型。

至少需要表达：

```text
users
sessions
devices
device_members
interfaces
aggregation_members
device_poll_runs
device_metrics
interface_metrics
device_reachability_incidents
interface_state_incidents
irf_member_observations
weekly_reports
report_jobs
system_settings
worker_heartbeat
```

允许实现时合并表，但不得丢失本规范要求的语义。

所有时间字段使用 `TIMESTAMPTZ`。

所有数据库结构变化通过 Alembic。

---

## 24. Retention

保留策略：

```text
device/interface raw metrics      90 days
device poll runs                  at least 90 days
device/interface incident records long-term
IRF incident-relevant history     long-term
weekly report metadata            long-term
weekly DOCX files                 long-term
```

90 天原始数据清理必须分批执行，避免长事务和长时间锁表。

当前数据规模使用普通 PostgreSQL 表和必要索引即可。

---

## 25. Runtime architecture

目标环境：

```text
Debian 13 amd64
Docker Engine
Docker Compose Plugin
PostgreSQL
Python 3.14
FastAPI
SQLAlchemy 2.x
psycopg 3
Alembic
PySNMP 7.x
Netmiko
python-docx
```

Compose 服务固定为：

```text
web
worker
postgres
```

`web` 与 `worker` 使用同一应用镜像、不同启动命令。

### web responsibilities

- health endpoint。
- login/session。
- report list/download/regenerate。
- priority interface configuration。

### worker responsibilities

- DEVICE_POLL。
- IRF observation。
- metric processing。
- incident state updates。
- retention cleanup。
- weekly statistics。
- DOCX generation。
- report retry。
- heartbeat。

生产运行必须在无 Internet 条件下持续工作。

---

## 26. Installation and update

### 26.1 Directory layout

```text
/opt/network-report   application / compose
/data/network-report  database volume / report files
/etc/network-report   devices.toml / secrets.env / runtime config
```

### 26.2 install.sh

至少完成：

1. 检查 Debian 13 amd64。
2. 检查 Docker Engine / Compose Plugin 可用。
3. 创建目录。
4. 检查配置和 Secret 文件权限。
5. 启动 PostgreSQL。
6. 执行 `alembic upgrade head`。
7. 初始化单管理员账号。
8. 同步设备清单。
9. 启动 web / worker。
10. 验证 web health。
11. 验证 worker heartbeat。

### 26.3 update.sh

至少完成：

1. 检查目标应用镜像存在。
2. 更新应用容器。
3. 执行 `alembic upgrade head`。
4. 启动 web / worker。
5. 验证 health。
6. 验证 worker heartbeat。

---

## 27. Reliability requirements

周报可靠性是最高优先级验收要求。

必须保证：

1. Worker 重启不丢失周报生成责任。
2. 同一周不会并发生成两个报告任务。
3. 报告失败有状态、有错误、有自动重试。
4. 手工重新生成幂等。
5. DOCX 使用临时文件和原子替换。
6. 数据不足仍生成 DOCX，并明确展示数据缺失与 Coverage。
7. 单设备失败不影响其他设备采集。
8. 单 section 失败允许保留有效数据并形成 PARTIAL。
9. PostgreSQL 短暂不可用后 Worker 能继续恢复循环。
10. 报告统计只读取持久化数据。
11. 5 分钟调度不会对同一设备产生重叠采集。
12. Worker 启动后不会补跑大量过期实时采集周期，只继续未来周期；周报则按持久化任务状态恢复。

---

## 28. Acceptance

系统交付至少满足：

1. 独立 S10500X 真实采集通过。
2. S10500X IRF 真实采集通过。
3. S12500 IRF 真实采集通过。
4. 设备 CPU、内存、接口状态和计数器能够稳定持久化。
5. 聚合接口和成员关系能够正确识别。
6. Web 能手工选择重点接口，聚合口不会自动选择成员口。
7. 5 分钟采集连续运行至少两个完整报告周，无系统性中断。
8. CPU/内存平均值、最大值、P95 与人工抽查一致。
9. 接口利用率与人工抽查一致，Counter Reset 不产生假峰值。
10. 设备 Down / Recovery 符合 2-cycle 规则。
11. 重点接口 Down / Recovery 符合 2-cycle 规则。
12. IRF 成员缺失能在周报中体现。
13. Monitoring Coverage expected/success/partial/failed 计算正确。
14. Coverage <95% 时报告标记“数据完整性不足”，但仍成功生成。
15. 每周一 00:10 自动生成上一完整周 DOCX。
16. 注入一次 DOCX 生成失败后，10 分钟重试能够最终成功。
17. 报告前重启 Worker，周报任务仍能自动恢复并生成。
18. 同周手工重新生成后服务器只保留一个当前 DOCX。
19. Web 登录后可以查看、下载、重新生成报告并配置重点接口。
20. DOCX 包含固定 8 个章节，“本周处理问题”区域可手工编辑。
21. 生产环境断开 Internet 后系统仍能采集、统计、登录和生成报告。
22. `install.sh` 和 `update.sh` 在目标环境验证通过。
