# 真实环境验收（W05-T005 / Release Gate 证据）

本文件只定义**验收条目、判定标准与证据模板**。它不是验收结论：在真实环境
取得证据之前，W05-T005 不得 REVIEW_PASSED，W05-GATE 保持 BLOCKED，且不得
以任何合成/模拟数据替代（Owner 指令 2026-09-06；AGENTS.md real-device
evidence rule）。

证据采集工具：`scripts/acceptance_evidence.sh`（只读；在目标生产机上对已
安装栈运行，输出 markdown 证据块）。人工核对类条目（A15/A16）按模板另附
记录。证据文件放入 `docs/evidence/<ID>.md`（含采集时间、主机、执行人）。

## 条目与判定标准

| ID | 条目 | 证据来源 | 判定标准 |
| --- | --- | --- | --- |
| A01 | Debian 13 amd64 安装 | install.sh 全程输出 + 退出码 0 + `/etc/os-release` | 第 3 节安装检查清单全部通过（OPERATIONS.md） |
| A02 | 离线运行 | 断网/气隙状态下：/health、心跳、报告列表、采集（poll runs 持续增长） | 全部正常，无任何因外网缺失产生的错误 |
| A03 | 10 个逻辑设备 | evidence 脚本 inventory 节 | `total_devices=10`（8 独立 S10500X + 1 组 S10500X IRF + 1 组 S12500 IRF），`expected_irf_members` 与 §2.2 一致 |
| A04 | 独立 S10500X 采集 | 该设备 poll runs 24h 统计 + failed_sections | SUCCESS/PARTIAL ≥ 95%/天，无持续 `credentials` |
| A05 | S10500X IRF 采集 | 同上 + irf_member_observations | 双成员均被观察到；成员缺失可独立体现 |
| A06 | S12500 IRF 采集 | 同上 | 同 A05 |
| A07 | 聚合接口/重点接口配置 | Web 页面操作记录：聚合关系显示、只勾选聚合口不级联成员 | §11/§12 行为符合；monitored 状态持久 |
| A08 | ≥ 2 个完整报告周连续采集 | 连续 14 天 poll runs + 2 个 weekly_reports 行 | 无系统性中断（单点失败有 PARTIAL/FAILED 记录可解释） |
| A09 | 连续 2 个周一 00:10 自动报告 | evidence 脚本 scheduled report jobs 节（`report_jobs` 历史）：连续 2 个不同 `week_code` 均存在 `trigger=scheduled` 且 `status=succeeded` 的任务行，`created_at`（任务创建时间）落在该周次对应的周一 00:10 Asia/Shanghai ±5min（`started_at`/`finished_at`/`attempts` 一并记录）；`weekly_reports` success 行与 DOCX 仅作结果佐证，不得作为 scheduled 触发的历史证据 | 触发时间与状态符合；manual regenerate 会更新 `weekly_reports.generated_at`，但 `report_jobs` 的 scheduled 历史不受影响（W05-AUDIT-2） |
| A10 | DOCX 失败 → 10 分钟重试 | 注入一次失败（如临时只读 reports 目录）后的 report_jobs 时间线 | 失败有状态+错误摘要，10 分钟重试最终成功，恢复后文件可下载 |
| A11 | Worker 重启恢复 | 周一 00:10 前重启 worker 容器 | 任务自动恢复并生成；不补跑过期实时采集 |
| A12 | regenerate one-current | 同一周手工重新生成 | 服务器始终只有一个当前 DOCX；旧文件在成功前一直可下载 |
| A13 | 90 天清理 | evidence 脚本 retention 节（或 seeded 老数据验证） | 三张原始表最老行（`MIN` collected_at / cycle_started_at，即 oldest row 年龄）≤ ~90 天（脚本判定值 BEYOND-90d/OK）；长期表不受影响 |
| A14 | update.sh 全流程 | 升级记录：镜像→迁移→重启→/health→心跳，退出码 0 | 按第 4 节升级清单执行；含一次真实 schema 迁移 |
| A15 | 真实周报人工核对（W03-T010） | A15 模板人工核对记录 | period 正确 / 数值与抽查一致 / 8 章节完整 / 缺失数据显式 / 无 Secret 泄漏 / 本周处理问题可编辑 |
| A16 | 真机字段验证（W01-T007） | 独立 S10500X + S10500X IRF + S12500 IRF 的采集核对记录（脱敏 fixture 已固化） | W01-T007 最小验证集合全部关闭 |

A15/A16 完成后，W01-T007 与 W03-T010 方可关闭；三者齐备且无 P0/P1 缺陷时
W05-GATE 才可评审。

## 证据记录模板

```markdown
# <ID> — <条目名>

- 日期/时间（Asia/Shanghai）：
- 主机与环境（可脱敏）：
- 执行人：
- 操作/命令（原样粘贴，含退出码）：
- 输出（粘贴 scripts/acceptance_evidence.sh 相关节或截图路径）：
- 与判定标准逐条对照：
- 结论：PASS / FAIL（FAIL 附原因与复现步骤）
- 附件：docs/evidence/ 下的原始输出文件名
```

### A15 真实周报人工核对模板

```markdown
# A15 — 真实周报人工核对（对应周：YYYY-Wnn）

- 报告文件：network-weekly-report-YYYY-Wnn.docx
- 对照源数据：设备侧人工抽查记录（CPU/内存/接口计数器截图或 CLI 输出）
1. period：[YYYY-MM-DD 周一 00:00, YYYY-MM-DD 下周一 00:00) —— 与日历一致？
2. 数值抽查：设备 CPU avg/max/P95、内存、Top10 接口利用率、CRC/Error/Drop
   增量 —— 与源数据抽查一致（列出 3 处以上对照）？
3. 8 个章节全部存在且标题正确？
4. 缺失数据处显示「数据缺失」而非 0/上一采样值？
5. 全文与元数据中无 community/密码/主机敏感信息泄漏？
6. 第 8 部分「本周处理问题」空白区域可在 Word/WPS 中编辑？
- 核对人/日期：
```

### A16 真机字段验证模板（W01-T007 关闭记录）

```markdown
# A16 — 真机采集验证

- 独立 S10500X：SNMP 可达 / SSH 只读命令通过 / identity+version 归一化 /
  CPU、内存 / 接口发现 / 聚合关系 / 计数器 —— 附输出
- S10500X IRF（2 成员）：成员识别 / 角色识别 —— 附输出
- S12500 IRF（2 成员）：同上 —— 附输出
- fixture 固化：tests/fixtures/h3c/ 下已按脱敏规则入库（列出文件）
- Secret 泄漏检查：输出与 fixture 中 0 处 community/密码/管理 IP
- W01-T007 关联：TASK_GRAPH.md 状态由 BLOCKED — FIELD_VALIDATION_PENDING
  变更为 CLOSED 的 commit：
```

## 当前状态

```text
W05-T005: IN_PROGRESS — 工具/清单/模板已就绪；等待真实环境证据。
  A01..A16 全部 PENDING（不得伪造）。
W05-GATE: BLOCKED — 依赖 W05-T005 全部 PASS + W01-T007 CLOSED +
  W03-T010 CLOSED + 无 P0/P1 缺陷。
```
