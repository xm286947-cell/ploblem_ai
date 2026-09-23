import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from quality_knowledge.web.app import create_app


HEADERS = {
    "HMI": ("TRC单号", "问题描述", "产品系列"),
    "PLC": ("ITR单号", "问题描述", "问题原因定位"),
    "IFA": ("ITR单号", "问题描述", "Bug所属业务组"),
}


@pytest.mark.parametrize("business_type", ["HMI", "PLC", "IFA"])
def test_realistic_excel_preview_confirm_e2e(tmp_path, business_type):
    db = tmp_path / f"{business_type}.db"
    excel = tmp_path / f"{business_type.lower()}_acceptance.xlsx"
    wb = Workbook(); ws = wb.active; ws.title = "质量问题"
    ws.append(HEADERS[business_type])
    ws.append((f"{business_type}-P3-001", "P3真实链路验收问题", "验收数据"))
    wb.save(excel)

    client = TestClient(create_app(db))
    with excel.open("rb") as stream:
        preview = client.post("/import/preview", data={"business_type": business_type},
                              files={"file": (excel.name, stream, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert preview.status_code == 200
    assert "尚未写入正式 Knowledge" in preview.text
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0] == 0
    session = re.search(r'name="intake_session_id" value="([^"]+)"', preview.text)
    assert session, preview.text
    confirm = client.post("/import/confirm", data={"intake_session_id": session.group(1)}, follow_redirects=True)
    assert confirm.status_code == 200
    assert "导入结果" in confirm.text and "COMPLETED" in confirm.text
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT business_type,business_issue_id FROM quality_issue").fetchone()
        assert row == (business_type, f"{business_type}-P3-001")


def test_confirm_is_idempotent_in_release_acceptance(tmp_path):
    db, excel = tmp_path / "release.db", tmp_path / "plc.xlsx"
    wb = Workbook(); ws = wb.active; ws.append(HEADERS["PLC"]); ws.append(("PLC-P3-IDEMP", "幂等验收", "原因")); wb.save(excel)
    client = TestClient(create_app(db))
    with excel.open("rb") as stream:
        response = client.post("/import/preview", data={"business_type": "PLC"}, files={"file": (excel.name, stream)})
    session = re.search(r'name="intake_session_id" value="([^"]+)"', response.text).group(1)
    first = client.post("/import/confirm", data={"intake_session_id": session}, follow_redirects=False)
    second = client.post("/import/confirm", data={"intake_session_id": session}, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0] == 1
