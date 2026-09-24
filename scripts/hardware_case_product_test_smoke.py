from __future__ import annotations

import tempfile
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.web.p0_app import create_p0_app
from scripts.hardware_case_mvp_smoke import main as backend_smoke
from scripts.hardware_case_precheck import check_python, check_web
from services.hardware_case_runtime_adapter import build_hardware_case_structurer


def frontend_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="hardware-case-product-test-") as temp:
        root = Path(temp)
        p0_db = root / "quality_capability_p1.db"
        hardware_db = root / "hardware_case_mvp.db"
        app = create_p0_app(
            p0_db,
            hardware_case_db_path=hardware_db,
            hardware_tree_upload_dir=root / "tree_uploads",
            enabled_domains={"HARDWARE_CASE"},
        )
        assert app.state.p0_repository is None
        assert app.state.repeat_risk_service is None
        client = TestClient(app)
        page = client.get("/p0/hardware-cases")
        assert page.status_code == 200, page.text
        assert "HARDWARE CASE · P01" in page.text
        assert "硬件案例库" in page.text
        assert "双树导航" in page.text
        assert "案例搜索" in page.text

        tree = client.get("/p0/hardware-cases/tree")
        search = client.get("/p0/hardware-cases/search")
        detail = client.get("/p0/hardware-cases/HC-SMOKE")
        review = client.get("/p0/hardware-cases/review")
        base_data = client.get("/p0/hardware-cases/base-data")
        assert tree.status_code == 200 and "HARDWARE CASE · P02" in tree.text
        assert search.status_code == 200 and "HARDWARE CASE · P03" in search.text
        assert detail.status_code == 200 and "HARDWARE CASE · P04" in detail.text
        assert "EVIDENCE · P06" in detail.text
        assert review.status_code == 200 and "HARDWARE CASE · P05" in review.text
        assert base_data.status_code == 200 and "HARDWARE CASE · P07" in base_data.text
        assert "Change Diff" in base_data.text
        assert "APPLIED_WITH_EXCLUSIONS" in base_data.text
        assert "DELETE" not in base_data.text

        assert client.get("/p0/static/hardware_case.js").status_code == 200
        assert client.get("/p0/static/hardware_case.css").status_code == 200
        assert client.get("/p0/static/hardware_tree_import.js").status_code == 200
        assert client.get("/p0/static/hardware_tree_import.css").status_code == 200


def runtime_package_smoke() -> None:
    required = (
        ROOT / "config/runtime/model.local.hardware_case.example.yaml",
        ROOT / "config/runtime/agents/hardware_case.structure.yaml",
        ROOT / "prompts/runtime/hardware_case/structure_v1.md",
        ROOT / "services/hardware_case_runtime_adapter.py",
        ROOT / "INIT_LOCAL_CONFIG.bat",
        ROOT / "CHECK_ENV.bat",
        ROOT / "START_HARDWARE_CASE.bat",
        ROOT / "RUN_REAL_AI_VALIDATION.bat",
        ROOT / "scripts/hardware_case_web_start.py",
    )
    assert all(path.is_file() for path in required)
    assert build_hardware_case_structurer.__hardware_case_structurer_factory__ is True
    assert check_python() == []
    assert check_web() == []


def main() -> int:
    code = backend_smoke()
    if code != 0:
        return code
    frontend_smoke()
    runtime_package_smoke()
    print("PRODUCT_TEST_PACKAGE=PASS")
    print("P01_P07_FRONTEND=PASS")
    print("RUNTIME_PACKAGE_ASSETS=PASS")
    print("STARTUP_PRECHECK=PASS")
    print("PACKAGE_STATUS=READY_FOR_INTERNAL_TEST")
    print("RELEASE_CLAIM=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
