from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
import sys

import openpyxl
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.web.p0_app import create_p0_app


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}
TREE_MAINTAINER = {
    "X-Hardware-Case-Role": "MAINTAINER",
    "X-Hardware-Case-Operator": "package-smoke",
}


def tree_workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "分类"
    sheet.append(["编码", "一级", "二级", "备注"])
    sheet.append(["C-IN-PROTECT", "电源", "输入保护", "Synthetic"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def field(value: str) -> dict:
    return {
        "candidate_value": value,
        "confirmed_value": None,
        "review_disposition": "UNREVIEWED",
        "evidence_refs": [],
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hardware-case-mvp-") as temp:
        root = Path(temp)
        p0_db = root / "quality_capability_p0.db"
        hardware_db = root / "hardware_case_mvp.db"

        initializer = P0Initializer(
            manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
            plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
        )
        initializer.initialize(p0_db)

        app = create_p0_app(
            p0_db,
            stage_runner=object(),
            hardware_case_db_path=hardware_db,
        )
        client = TestClient(app)

        assert client.get("/api/v2/initialization/status").status_code == 200
        assert client.get("/api/v2/products").status_code == 200

        case = {
            "case_id": "HC-PACKAGE-SMOKE-001",
            "title": "Synthetic package smoke",
            "case_status": "PENDING_REVIEW",
            "processing_status": "READY",
            "source_refs": ["word:synthetic-package-smoke.docx"],
            "product_context": {"product": "Synthetic Controller"},
            "facts": {
                "symptom": field("上电后复位"),
                "root_cause": field("输入浪涌触发保护"),
                "actions": field("增加输入保护"),
            },
        }
        created = client.post(
            "/api/v2/hardware-cases",
            json=case,
            headers=MAINTAINER,
        )
        assert created.status_code == 201, created.text

        # Fail closed before human confirmation and publish.
        assert client.get(
            "/api/v2/hardware-cases?q=浪涌"
        ).json()["results"] == []

        # Productized tree intake: upload -> mapping -> preview/diff -> confirm -> apply.
        upload = client.post(
            "/api/v2/hardware-cases/tree-imports",
            data={"tree_type": "CIRCUIT_FEATURE"},
            files={
                "file": (
                    "synthetic-package-tree.xlsx",
                    tree_workbook_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            headers=TREE_MAINTAINER,
        )
        assert upload.status_code == 201, upload.text
        job_id = upload.json()["job"]["job_id"]

        analyzed = client.post(
            f"/api/v2/hardware-cases/tree-imports/{job_id}/analyze",
            json={
                "sheet_name": "分类",
                "header_row": 1,
                "path_columns": ["一级", "二级"],
                "metadata_columns": ["备注"],
                "business_key_column": "编码",
            },
            headers=TREE_MAINTAINER,
        )
        assert analyzed.status_code == 200, analyzed.text
        analysis = analyzed.json()
        assert analysis["job"]["status"] == "REVIEW_REQUIRED"
        assert analysis["change_summary"]["ADD"] == 2

        for change in analysis["changes"]:
            if change["change_type"] == "ADD":
                decision = client.post(
                    f"/api/v2/hardware-cases/tree-imports/{job_id}"
                    f"/changes/{change['change_id']}/decision",
                    json={"decision": "CONFIRMED"},
                    headers=TREE_MAINTAINER,
                )
                assert decision.status_code == 200, decision.text

        ready = client.post(
            f"/api/v2/hardware-cases/tree-imports/{job_id}/ready",
            headers=TREE_MAINTAINER,
        )
        assert ready.status_code == 200, ready.text

        applied = client.post(
            f"/api/v2/hardware-cases/tree-imports/{job_id}/apply",
            headers=TREE_MAINTAINER,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["active_version"]["version_id"] == "C-001"

        tree = client.get("/api/v2/hardware-cases/trees/CIRCUIT_FEATURE")
        assert tree.status_code == 200
        imported_node = next(
            node
            for node in tree.json()["nodes"]
            if node["path"] == ["电源", "输入保护"]
        )
        imported_node_id = imported_node["node_id"]

        for name, value in (
            ("symptom", "上电后复位"),
            ("root_cause", "输入浪涌触发保护"),
            ("actions", "增加输入保护"),
        ):
            response = client.post(
                "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001/review",
                json={
                    "field_name": name,
                    "disposition": "CONFIRMED",
                    "confirmed_value": value,
                },
                headers=MAINTAINER,
            )
            assert response.status_code == 200, response.text

        evidence = {
            "evidence_id": "EV-PACKAGE-001",
            "source_ref": "word:synthetic-package-smoke.docx",
            "evidence_type": "TEXT",
            "locator": {"section": "原因分析", "block_id": "B0001"},
            "excerpt_or_caption": "输入浪涌触发保护",
            "evidence_status": "AVAILABLE",
        }
        assert client.post(
            "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001/evidence",
            json=evidence,
            headers=MAINTAINER,
        ).status_code == 201

        mapping = {
            "mapping_id": "MAP-PACKAGE-001",
            "tree_type": "CIRCUIT_FEATURE",
            "node_id": imported_node_id,
            "relation_role": "PRIMARY",
            "mapping_status": "CONFIRMED",
            "confidence": 1.0,
            "basis_refs": ["EV-PACKAGE-001"],
        }
        assert client.post(
            "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001/mappings",
            json=mapping,
            headers=MAINTAINER,
        ).status_code == 201

        gate = client.get(
            "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001/publish-gate",
            headers=MAINTAINER,
        )
        assert gate.status_code == 200 and gate.json()["passed"] is True

        published = client.post(
            "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001/publish",
            headers=MAINTAINER,
        )
        assert published.status_code == 200
        assert published.json()["case_status"] == "PUBLISHED"

        search = client.get("/api/v2/hardware-cases?q=浪涌")
        assert search.status_code == 200
        assert search.json()["results"][0]["case_id"] == "HC-PACKAGE-SMOKE-001"

        detail = client.get(
            "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001"
        )
        assert detail.status_code == 200
        assert detail.json()["facts"]["root_cause"] == "输入浪涌触发保护"

        evidence_view = client.get(
            "/api/v2/hardware-cases/HC-PACKAGE-SMOKE-001/evidence"
        )
        assert evidence_view.status_code == 200
        assert evidence_view.json()["evidence"]

        print("RESULT=PASS")
        print("PACKAGE_STAGE=RC0_PREP")
        print(
            "GOLDEN_PATH=Excel Upload -> Mapping -> Preview/Diff -> Apply -> "
            "Case -> Human Confirm -> Evidence -> Tree Mapping -> Publish -> "
            "Search -> Detail -> Evidence"
        )
        print("TREE_IMPORT_VERSION=C-001")
        print("MVP_RELEASE_CLAIM=NO")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
