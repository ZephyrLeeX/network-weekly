# Network Weekly Report System

网络运维自动周报系统。产品与架构需求见 `SYSTEM_SPEC.md`，任务状态见
`TASK_GRAPH.md` 与 `EXECUTION_STATE.md`。

当前阶段与进行中的 Task 以 `EXECUTION_STATE.md` / `TASK_GRAPH.md` 为准。

## Toolchain

- Python 3.14（`.python-version`）
- 依赖管理：uv（`pyproject.toml` + `uv.lock`）
- 测试：pytest，Lint：ruff，类型检查：mypy

## Commands

```bash
# 安装/同步依赖（严格按锁文件，不升级）
uv sync --frozen

# 单元测试（默认不含需要 PostgreSQL 的集成测试）
uv run pytest

# Lint
uv run ruff check .

# 类型检查
uv run mypy
```

### 集成测试（需要真实 PostgreSQL）

先启动开发用 Compose 栈（Wave 0 之后提供 `docker-compose.yml`），然后：

```bash
docker compose up -d postgres
uv run pytest -m integration
```

集成测试使用 `NETWORK_REPORT_TEST_DATABASE_URL`（默认指向本地 Compose 的
PostgreSQL），并真实执行 `alembic upgrade head`，不使用 SQLite 替代。

测试会对将执行 `DROP/CREATE DATABASE` 的目标做硬保护：数据库名必须匹配
`*_test` / `test_*`（无条件），主机默认只允许 `localhost` / `127.0.0.1` /
`::1`；非回环主机需显式设置 `NETWORK_REPORT_ALLOW_DESTRUCTIVE_TESTS=YES`。
数据库名一律通过 `psycopg.sql.Identifier` 绑定，不做字符串拼接。

## Runtime（Wave 0）

```bash
cp .env.example .env   # 填写本地开发值（.env 不入库）
docker compose up -d --build
docker compose run --rm web alembic upgrade head   # schema 全部由迁移驱动
```

- `web`: `GET /health` 返回应用状态与数据库连通性（不含任何 Secret/环境变量）。
- `worker`: 启动后周期写入 `worker_heartbeat` 表（`worker_id` 主键 upsert），
  与 web health 相互独立；PostgreSQL 短暂不可用时继续循环重试。
- 日志：web 与 worker 都在启动时接入集中式 secret 脱敏；普通日志、Uvicorn
  access/error 日志以及 exception traceback（密码 / SNMP community / 私钥 /
  Authorization / 已注册 Secret 值）统一脱敏，traceback 与诊断信息保留，
  配置的数据库口令在进程启动时自动注册。
- 报告目录：`reports` named volume 由镜像内 app 用户（uid 1000）拥有的
  `/var/lib/network-report/reports` 初始化，非 root 容器可直接读写。

### 运行时配置（环境变量，见 `backend/config.py`）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 本地 Compose PostgreSQL | SQLAlchemy + psycopg 3 连接串（口令需 URL 编码） |
| `NETWORK_REPORT_ENVIRONMENT` | `development` | 运行环境标识 |
| `NETWORK_REPORT_DATA_DIR` | `./data` | 持久化根目录（报告位于 `<data>/reports`） |
| `NETWORK_REPORT_TIMEZONE` | `Asia/Shanghai` | 业务时区（SYSTEM_SPEC §3，显式指定） |
| `NETWORK_REPORT_LOG_LEVEL` | `INFO` | 日志级别 |
| `NETWORK_REPORT_WORKER_ID` | `worker` | Worker 身份标识 |
| `NETWORK_REPORT_HEARTBEAT_INTERVAL_SECONDS` | `30` | 心跳周期（秒，必须为正） |

## 仓库布局

```text
application/backend/   应用后端 Python 包（backend.*）
migrations/            Alembic 迁移（W00-T004 起生效）
tests/                 单元测试（tests/unit）与 PostgreSQL 集成测试（tests/integration）
scripts/               部署/运维脚本（Wave 5 填充）
docs/                  文档（Wave 5 运维手册等）
data/                  本地运行时数据（gitignore，不入库）
```
