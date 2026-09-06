# 运维手册（Operator Runbook）

面向日常运维的检查与排查手册。布局与安装机制详见 `deploy/README.md`；
产品语义以 `SYSTEM_SPEC.md` 为准。**本手册与系统日志一样，不包含任何真实
Secret 值**——所有示例中的凭据一律用占位符表示。

## 0. 速查

```bash
# 统一的 compose 入口（建议设为 shell alias `nrc`）
nrc() { docker compose -p network-report \
  --project-directory /opt/network-report \
  --env-file /opt/network-report/.env \
  -f /opt/network-report/docker-compose.yml "$@" ; }

nrc ps                 # 服务状态
nrc logs -f worker     # 跟踪 worker 日志
docker load -i <镜像tar>   # 离线导入新镜像（生产机不访问 Internet）
deploy/install.sh          # 安装 / 重复安装（幂等）
deploy/update.sh --image <新镜像>   # 升级
# 回滚到上一镜像（仅在上一镜像兼容当前数据库 schema 时可直接执行；
# 若上一镜像的迁移已执行且不兼容新 schema，须先还原数据库备份 — 见第 5 节）:
deploy/update.sh --image "$(grep '^NETWORK_REPORT_APP_IMAGE=' /opt/network-report/.env.bak | cut -d= -f2-)"
```

install.sh / update.sh 退出码：`2` 用法错误；`10` 主机/系统；`11`
Docker/Compose；`12` 镜像缺失（离线导入）；`13` 配置或 Secret 文件问题；
`14` 迁移失败（update 已自动还原 .env，未重启任何容器）；`15` 管理员初始
化；`16` 设备清单同步；`17` 服务启动；`18` web /health；`19` worker 心跳。

## 1. 目录与持久化

| 路径 | 内容 | 升级/替换容器时 |
| --- | --- | --- |
| `/opt/network-report` | docker-compose.yml + `.env`(0600，含自动生成的 PostgreSQL 口令) | compose 文件会被 update 重写；`.env` 保留，旧副本存于 `.env.bak` |
| `/data/network-report/postgres` | PostgreSQL 数据 | 不变 |
| `/data/network-report/reports` | 生成的 DOCX（当前文件 + 极少数待处理 candidate） | 不变 |
| `/etc/network-report/devices.toml` | 设备清单（非敏感，0644） | 不变 |
| `/etc/network-report/secrets.env` | 设备凭据（**0600**，属主 uid 1000） | 不变 |

## 2. 每日健康检查

**web /health**（应输出 `"status":"ok"` 且 `"database":"ok"`）：

```bash
docker exec network-report-web-1 python -c \
  "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)))"
```

**worker 心跳**（与 web 健康相互独立；`age` 应小于心跳间隔的 4 倍，默认
30s 间隔下 < 2 分钟）：

```bash
docker exec -it network-report-postgres-1 psql -U network_report -d network_report \
  -c "SELECT worker_id, version, last_heartbeat, now()-last_heartbeat AS age FROM worker_heartbeat;"
```

两项都异常时先看 `nrc logs --tail 100 worker` / `postgres`，再确认
`nrc ps` 的 healthy 状态与宿主机磁盘（`df -h /data`）。

## 3. 安装检查清单（离线，Debian 13 amd64）

1. 目标机已装 Docker Engine + Compose 插件（`docker info`、
   `docker compose version`）；本脚本不安装任何系统包、不联网。
2. 导入两个镜像并确认存在：`docker load -i <app镜像tar>`；
   `docker image inspect network-weekly-app:<版本>` 与
   `docker image inspect postgres:17-alpine` 都成功。
3. root 运行 `deploy/install.sh`。可重复执行：已有 `.env`（PostgreSQL 口
   令）、配置文件、管理员账号都会保留。
4. 安装后核对：
   - `nrc ps` 三个服务均 Up（web/postgres healthy）；
   - 第 2 节两项检查通过；
   - `stat -c '%a' /etc/network-report/secrets.env` 输出 `600`；
   - **上线前**：编辑 `/etc/network-report/devices.toml` 填真实管理 IP，
     编辑 `secrets.env` 填真实凭据（**绝不使用示例占位值**；worker 每个采
     集周期重新加载，改完无需重启）。设备清单改动后重跑一次 install.sh
     （幂等）完成 inventory sync。
5. 浏览器打开 `http://<主机>:8000/`，用安装时输入的管理员账号登录。

## 4. 升级检查清单

**升级前**：
1. 第 2 节健康检查通过；`df -h /data` 有充足空间。
2. 备份数据库（迁移是前进式的，备份是回滚兜底）：

```bash
docker exec -i network-report-postgres-1 \
  pg_dump -U network_report network_report > /root/network-report-$(date +%F).sql
```

3. `docker load` 导入新镜像，确认 `docker image inspect <新镜像>` 成功。

**升级**：`deploy/update.sh --image <新镜像>`（缺省用 `.env`/环境变量中的
镜像）。流程：本地镜像检查 → `.env` 切换（旧副本存 `.env.bak`）→ 在新镜
像内 `alembic upgrade head` → 重建 web/worker → /health → 心跳。

**升级后**：退出码 0；`nrc ps` healthy；第 2 节检查通过；`/health` 的
`version` 已变为新版本；报告列表能打开、最近一周 DOCX 可下载且文件仍在
`/data/network-report/reports`（升级不删除任何 DOCX）。

**升级失败**：按退出码定位（见第 0 节），并区分两个阶段：

- **迁移失败（14）**：`.env` 已自动还原，未重启任何容器，旧 stack 不受
  影响，仍在正常运行；`nrc logs` 查看原因后修复镜像重试。此阶段直接回退
  旧镜像是安全的（迁移未提交）。
- **迁移成功后 restart / health / 心跳失败（17/18/19）**：迁移**已提交**，
  schema 可能已前进，**不要盲目回退应用镜像**。update.sh 会打印
  schema-aware 处置提示：先确认上一镜像能否运行于已迁移的 schema——能，
  按第 5 节回退；不能，先还原升级前数据库备份（第 5 节，危险操作），再
  启动旧镜像。

**compose/部署配置变更**（如日志参数、端口）随仓库新版本分发：重跑一次
`deploy/install.sh`（幂等，只会刷新 `/opt` 下的 compose 文件并保留 `.env`
数据），随后 `nrc up -d --wait` 使新配置生效。

## 5. 回滚

```bash
# 上一镜像始终记录在 .env.bak（update.sh 每次更新时写入）
deploy/update.sh --image "$(grep '^NETWORK_REPORT_APP_IMAGE=' /opt/network-report/.env.bak | cut -d= -f2-)"
```

按迁移是否已提交分两种情况（update.sh 的失败提示会标明当前处于哪种）：

- **迁移尚未提交**（如镜像检查、迁移失败：退出码 12/14）：旧 stack 未受
  影响、仍在正常运行，数据库 schema 未变——直接执行上面命令回退镜像是
  安全的。
- **迁移已提交**（升级后 restart / health / 心跳失败：退出码 17/18/19）：
  数据库 schema 可能已前进，**不得盲目回退应用镜像**。先判断上一镜像能否
  运行于已迁移的 schema：
  - 能兼容：直接执行上面命令。
  - 不能兼容（或无法确认）：**先还原升级前的数据库备份**（第 4 节
    pg_dump，危险操作，会覆盖当前数据，先二次确认），再执行上面命令启动
    旧镜像。

```bash
cat /root/network-report-<日期>.sql | docker exec -i network-report-postgres-1 \
  psql -U network_report -d network_report
```

- 系统不自动做数据库 downgrade，也不会自动还原数据库——两者都必须由操作
  员按上面步骤显式执行。
- `/data`、`/etc` 不受升级/回滚影响，无需处理。

## 6. 每周一例行

周一 00:10（Asia/Shanghai）自动生成上一完整周 DOCX。上午例行：

1. 打开 Web 报告列表：最新一周 status 应为「成功」；存在「重新生成失败/
   等待自动重试」等提示时按第 8 节处理。
2. 核对周期（周一至周日）与 Coverage；下载 DOCX，手工填写「本周处理问题」
   后发送（系统不上传用户改过的文件）。
3. 扫一眼第 9 节的 FAILED/PARTIAL 统计，有异常设备先排查。

## 7. 手工重新生成

Web 报告列表 → 该周「重新生成」。同一周服务器端始终只有一个当前 DOCX：
重新生成成功后原子替换；生成过程中旧文件始终可下载；同周不会并发两个生成
任务。列表上会显示该周最新任务状态（排队中/进行中/等待重试/失败 + 原因摘
要）。

## 8. 周报生成排查（job / retry / reconcile）

```bash
docker exec -it network-report-postgres-1 psql -U network_report -d network_report -c \
 "SELECT id, week_code, status, trigger, attempts, next_retry_at, left(last_error,100) AS last_error
  FROM report_jobs ORDER BY id DESC LIMIT 10;"
```

- 状态机：`pending → running → succeeded | failed`。`failed` 仍是**活跃**
  任务：每 10 分钟自动重试一次，成功即止；同一周最多一个活跃任务（数据库
  唯一约束保证）。
- 卡在 `running`：worker 重启时会自动把超时的 running 任务标记失败并进入
  重试（`recover_stale_running_jobs`）；若长时间不动，看 `nrc logs worker`
  中 weekly-report 线程的报错。
- 列表显示「新报告已提交，但文件切换待恢复…」（`succeeded_install_pending`
  状态）：DOCX 已生成，仅最后的文件切换未完成；worker 下一轮
  `reconcile_report_files` 会自动完成切换，无需人工干预；当前下载仍是上一
  份成功报告。
- DOCX 文件与 candidate：candidate 命名为 `<正式名>.docx.candidate.<任务
  id>`，只与生成它的那个任务绑定；成功后原子替换为当前文件，残留 candidate
  由 reconcile 自动清理。核对「注册表 vs 磁盘」：

```bash
docker exec -it network-report-postgres-1 psql -U network_report -d network_report -c \
 "SELECT week_code, status, file_path, generated_at FROM weekly_reports ORDER BY week_code DESC;"
ls -la /data/network-report/reports
```

`status='success'` 行的 `file_path` 必须存在且是完整 DOCX；磁盘上没有任何
`candidate` 残留即正常。

## 9. 采集排查（DEVICE_POLL / PARTIAL / FAILED）

每台逻辑设备每天应有 288 个计划周期（5 分钟一次），每周期必有一行：

```bash
docker exec -it network-report-postgres-1 psql -U network_report -d network_report -c \
 "SELECT d.name, pr.status, count(*) FROM device_poll_runs pr
  JOIN devices d ON d.id=pr.device_id
  WHERE pr.cycle_started_at >= now()-interval '1 day'
  GROUP BY 1,2 ORDER BY 1,2;"
docker exec -it network-report-postgres-1 psql -U network_report -d network_report -c \
 "SELECT d.name, pr.failed_sections, count(*) FROM device_poll_runs pr
  JOIN devices d ON d.id=pr.device_id
  WHERE pr.status IN ('PARTIAL','FAILED') AND pr.cycle_started_at >= now()-interval '1 day'
  GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20;"
```

- `SUCCESS`：核心采集成功；`PARTIAL`：已有有效数据入库、个别 section 失败
  （`failed_sections` 列出 section 名）；`FAILED`：本周期无可用数据。
- `failed_sections` 含 `credentials`：secrets 文件问题——检查
  `/etc/network-report/secrets.env` 权限（必须 0600）与该设备
  `credential_profile` 对应的键是否存在；worker 每周期重读文件，修复后自
  动恢复。
- `failed_sections` 含 `overlap`：上一周期还没跑完（设备响应过慢），通常
  瞬时，连续出现才需要关注。
- 语义提醒：**采集失败 ≠ 设备故障**。设备 DOWN/RECOVERED 由 2 连续周期
  SNMP+SSH 双失败/恢复的状态机判定，记录在
  `device_reachability_incidents`；PARTIAL 周期保留的有效数据参与周报统
  计；单设备失败不影响其他设备。

## 10. SNMP / SSH 排查

- secrets.env 键按凭据 profile 组织：`SNMP_COMMUNITY_<PROFILE>`、
  `SSH_USERNAME_<PROFILE>`、`SSH_PASSWORD_<PROFILE>`（profile 中的 `-` 映
  射为 `_`）。**任何地方（工单、聊天、文档）都不要粘贴真实值**；日志已统
  一脱敏注册过的 Secret，但不要故意把 Secret 打进日志。
- SNMP 失败时系统会做一次轻量 SSH 管理面确认：SSH 通 → 设备不算 DOWN，
  但该周期 SNMP 异常反映在 PARTIAL 与 Coverage 中；连续 2 周期双失败才判
  DOWN。
- SSH 仅允许只读白名单命令（设备身份/版本/IRF 成员）。SSH 排查方向：账号
  锁定、特权级别、设备端 ACL。
- SNMP 排查方向：community/profile 是否正确、设备端 SNMP ACL、UDP 161 可
  达性、MIB 视图是否含所需 OID。ping 通不代表 SNMP 可达。

## 11. 容量检查（DB / 磁盘）

```bash
df -h /data && du -sh /data/network-report/*
docker exec -it network-report-postgres-1 psql -U network_report -d network_report -c \
 "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables
  WHERE relname IN ('device_metrics','interface_metrics','device_poll_runs')
  ORDER BY pg_total_relation_size(relid) DESC;"
docker exec -it network-report-postgres-1 psql -U network_report -d network_report -c \
 "SELECT pg_size_pretty(pg_database_size('network_report'));"
```

- 原始数据（三张表）由 worker **每 6 小时**自动执行一次 90 天批量清理
  （分小批删除，避免长锁；`nrc logs worker | grep "retention pass"` 可见
  每次清理的删除行数）。事故记录、IRF 观察、周报元数据与 DOCX 长期保留，
  不在清理范围。
- 容器日志有界（每服务 10MB × 3 个文件），不会撑满磁盘；
  `docker system df` 看整体占用，旧镜像可 `docker image prune`（确认不再
  需要回滚后）。

## 12. Secrets 权限检查

```bash
stat -c '%a %u %n' /etc/network-report/secrets.env   # 期望: 600 1000
```

权限宽于 0600 时应用**拒绝加载**（worker 日志出现 secrets unavailable，
对应周期记为 FAILED/`credentials`），install.sh 也会拒绝继续并给出修复命
令（`chmod 0600 …`）。属主必须是部署服务账号 uid 1000（非 root 容器进程
需要可读）。devices.toml 非敏感，0644 即可。

## 13. 日志

- 查看：`nrc logs [-f] web|worker|postgres`。
- 轮转：json-file，每服务单文件 10MB、最多 3 个（生产 compose 固定）。
- 脱敏：密码、SNMP community、私钥、Authorization 与已注册 Secret 值在日
  志输出层统一替换，异常堆栈同样脱敏。发现日志出现疑似敏感值立即视为事故
  处理并轮换凭据。
