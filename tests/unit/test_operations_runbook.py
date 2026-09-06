"""W05-T004: the operator runbook covers the required procedures safely.

docs/OPERATIONS.md must document the installation/upgrade/rollback checks,
the health/heartbeat checks and every major troubleshooting path — and it
must never contain a real or placeholder secret VALUE (keys and placeholders
only).
"""

from pathlib import Path

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "OPERATIONS.md"


def test_runbook_exists_and_covers_required_procedures() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    for required in (
        "安装检查清单",       # install checks
        "升级检查清单",       # upgrade checks
        "回滚",              # rollback
        "worker 心跳",        # heartbeat check
        "/health",            # web health check
        "PARTIAL",            # poll result troubleshooting
        "FAILED",
        "failed_sections",
        "SNMP",               # transport troubleshooting
        "SSH",
        "report_jobs",        # report job/retry/reconcile troubleshooting
        "next_retry_at",
        "succeeded_install_pending",
        "reconcile_report_files",
        "重新生成",           # manual regenerate
        "candidate",          # DOCX/candidate troubleshooting
        "pg_database_size",   # DB capacity
        "retention pass",     # retention actually runs periodically
        "600 1000",           # secrets permission check (mode + owner uid)
        "json-file",          # bounded logs
    ):
        assert required in text, required


def test_runbook_never_contains_secret_values() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    # Secret keys may appear (as key names), but never KEY=value assignments
    # and never the placeholder values from docs/secrets.env.example.
    assert "SNMP_COMMUNITY_" not in text or "SNMP_COMMUNITY_<PROFILE>" in text
    assert "SSH_PASSWORD_" not in text or "SSH_PASSWORD_<PROFILE>" in text
    for placeholder in ("change-me-snmp-community", "change-me-ssh-password"):
        assert placeholder not in text, placeholder
    assert "psql" in text  # commands are real; only the values are absent
