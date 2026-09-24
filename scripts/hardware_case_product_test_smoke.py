from __future__ import annotations

import tempfile
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app
from scripts.hardware_case_mvp_smoke import main as backend_smoke
from scripts.hardware_case_precheck import check_python, check_web
from services.hardware_case_runtime_adapter import build_hardware_case_structurer


def frontend_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="hardware-case-product-test-") as temp:
        root = Path(temp)
        p0_db = root / "quality_capability_p1.db"
        hardware_db = root / "hardware_case_mvp.db"
        P0Initializer(
            manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
            plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
        ).initialize(p0_db)
        client = TestClient(
            create_p0_app(
                p0_db,
                stage_runner=object(),
                hardware_case_db_path=hardware_db,
                hardware_tree_upload_dir=root / "tree_uploads",
            )
        )
        page = client.get("/p0/hardware-cases/base-data")
        assert page.status_code == 200, page.text
        assert "基础数据管理" in page.text
        assert "Change Diff" in page.text
        assert "APPLIED_WITH_EXCLUSIONS" in page.text
        assert "DELETE" not in page.text
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
    print("P07_FRONTEND=PASS")
    print("RUNTIME_PACKAGE_ASSETS=PASS")
    print("STARTUP_PRECHECK=PASS")
    print("PACKAGE_STATUS=READY_FOR_INTERNAL_TEST")
    print("RELEASE_CLAIM=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
