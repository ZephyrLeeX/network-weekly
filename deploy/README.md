# Production deployment layout (W05-T001, SYSTEM_SPEC.md §26.1)

`deploy/` holds everything a Debian 13 amd64 target host needs for an
offline install: the production Compose file, `install.sh` (W05-T002) and
`update.sh` (W05-T003). Operator procedures live in `docs/OPERATIONS.md`
(W05-T004).

## Directory layout

```text
/opt/network-report            application / compose (owned by root)
  docker-compose.yml           installed copy of deploy/docker-compose.prod.yml
  .env                         Compose variables, mode 0600 (contains the
                               generated PostgreSQL password)
/data/network-report           persistent data (survives container replacement)
  postgres/                    PostgreSQL data directory (bind mount)
  reports/                     generated DOCX files (bind mount; owned by the
                               deployment service account uid 1000 = the
                               non-root user inside the app image)
/etc/network-report            runtime configuration (not replaced by updates)
  devices.toml                 device inventory, non-sensitive, 0644
  secrets.env                  device credentials, mode 0600, owned by the
                               deployment service account (uid 1000)
```

Container-visible paths are fixed: the app sees its report directory at
`/var/lib/network-report/reports` and its configuration at
`/etc/network-report/devices.toml` + `/etc/network-report/secrets.env`
(backend defaults, `backend/config.py`). The host-side paths above are set
once in `/opt/network-report/.env`:

```text
NETWORK_REPORT_APP_IMAGE=network-weekly-app:0.1.0   # pre-built, loaded offline
NETWORK_REPORT_DATA_DIR=/data/network-report
NETWORK_REPORT_CONFIG_DIR=/etc/network-report
```

## Persistence guarantees

| What | Where | Survives |
| --- | --- | --- |
| PostgreSQL data | `/data/network-report/postgres` | web/worker/postgres container replacement, `update.sh`, host reboot |
| Generated DOCX | `/data/network-report/reports` | web/worker container replacement, `update.sh`, host reboot |
| Device inventory + secrets | `/etc/network-report` | all container operations (files are bind-mounted read-only) |
| Admin account, sessions, reports metadata, incidents | PostgreSQL data directory | as PostgreSQL row data |

Nothing is stored in a container-writable layer or a named volume, so
`docker compose up -d --force-recreate`, image updates and container
replacement never lose database or report files.

`secrets.env` is mounted read-only into the worker only. The backend refuses
to load it when its permission is wider than `0600`
(`backend/secrets.py`, SYSTEM_SPEC.md §22.2). It must be owned by the
deployment service account (uid 1000) rather than root so the non-root
container process can read it while the mode stays 0600 — §22.2 allows
either owner.

## Offline runtime

- The Compose file has no `build:` sections and never pulls: both images
  (`NETWORK_REPORT_APP_IMAGE`, `postgres:17-alpine`) must already exist on
  the host (`install.sh` checks with `docker image inspect` and fails with a
  diagnostic otherwise).
- The application image vendors all Python dependencies at build time
  (`uv.lock` frozen); no dependency download happens at container runtime.

## Difference from the development stack (and the one-time copy)

The development `docker-compose.yml` in the repository root keeps PostgreSQL
under `./data/postgres` and DOCX files in the `reports` **named volume**
(volume seeding gives the non-root app user a writable directory without any
host setup). The production stack does NOT use named volumes: DOCX files
live at `/data/network-report/reports`, owned by uid 1000 (created by
`install.sh`).

Development data therefore does not appear in a production install. To carry
an existing dev `reports` volume over once, run as root on the target host:

```bash
docker run --rm -u root \
  -v network-weekly_reports:/from:ro \
  -v /data/network-report/reports:/to \
  "$NETWORK_REPORT_APP_IMAGE" cp -a /from/. /to/ \
  && chown -R 1000:1000 /data/network-report/reports
```

(Adjust the volume name to `docker volume ls` output.) PostgreSQL data is
not carried over from a dev `./data/postgres` directory: production starts
from an empty data directory and `install.sh` runs `alembic upgrade head`.
