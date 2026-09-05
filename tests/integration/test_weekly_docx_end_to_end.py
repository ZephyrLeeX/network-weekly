"""End-to-end integration test: persisted data -> statistics -> DOCX
(W03-T008 acceptance) over migrated PostgreSQL.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from backend.db.models import Device, DevicePollRun, DeviceReachabilityIncident
from backend.reporting.docx import MISSING, SECTION_TITLES, render_report_docx
from backend.reporting.period import period_for_iso_week
from backend.reporting.service import build_weekly_report_data

pytestmark = pytest.mark.integration

PERIOD = period_for_iso_week(2026, 36)


@pytest.fixture(autouse=True)
def _clean(db_engine: Engine) -> Iterator[None]:
    with Session(db_engine) as session:
        for model in (DevicePollRun, DeviceReachabilityIncident, Device):
            session.query(model).delete()
        session.commit()
        yield


def test_report_chain_renders_valid_docx_with_missing_data(
    db_engine: Engine, tmp_path: Path
) -> None:
    with Session(db_engine) as session:
        # One device with a couple of cycles, one silent device, one open
        # incident: report must still generate with explicit 数据缺失.
        talking = Device(
            name="core-talk",
            management_ip="192.0.2.21",
            model_family="s10500x",
            credential_profile="default",
        )
        silent = Device(
            name="core-silent",
            management_ip="192.0.2.22",
            model_family="s10500x",
            credential_profile="default",
        )
        session.add_all([talking, silent])
        session.flush()
        for i in range(3):
            session.add(
                DevicePollRun(
                    device_id=talking.id,
                    cycle_started_at=PERIOD.start + timedelta(minutes=5 * i),
                    status="SUCCESS",
                )
            )
        session.add(
            DeviceReachabilityIncident(
                device_id=silent.id,
                started_at=datetime(2026, 9, 3, tzinfo=UTC),
                recovered_at=None,
            )
        )
        session.commit()

        generated = datetime(2026, 9, 7, 0, 12, tzinfo=UTC)
        data = build_weekly_report_data(session, PERIOD, generated_at=generated)

    rendered = render_report_docx(data, tmp_path)
    assert rendered.path.name == "network-weekly-report-2026-W36.docx"

    document = Document(str(rendered.path))
    headings = [
        p.text
        for p in document.paragraphs
        if p.style is not None and p.style.name == "Heading 1"
    ]
    assert headings == list(SECTION_TITLES)

    body_text = "\n".join(p.text for p in document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                body_text += "\n" + cell.text
    assert MISSING in body_text  # silent device CPU/memory explicitly missing
    assert "仍处于 Down" in body_text  # ongoing incident visible
    assert "2026-W36" in body_text
    assert "数据完整性不足" in body_text  # far below 95% coverage

    # 本周处理问题 blank editable area: last table is an empty 1x1 grid cell.
    last_table = document.tables[-1]
    assert len(last_table.rows) == 1 and len(last_table.rows[0].cells) == 1
    assert last_table.rows[0].cells[0].text.strip() == ""

    # No secret-bearing material ever appears in the document (§22.2).
    for marker in ("community", "password", "secret", "snmp", "credential"):
        assert marker not in body_text.lower()
