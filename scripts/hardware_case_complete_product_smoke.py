from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app
from services.hardware_asset_repository import CandidateAssetRepository


MAINTAINER = {"X-Hardware-Case-Role": "MAINTAINER"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FACT_FIELDS = (
    "background", "symptom", "impact", "occurrence_condition",
    "analysis_process", "failure_mode", "root_cause",
    "failure_mechanism", "actions", "verification_result", "conclusion",
)
CONTEXT_FIELDS = (
    "primary_subject", "component_or_device", "interface", "signal",
    "peer_device_or_load",
)
REUSABLE_FIELDS = (
    "engineering_rule", "design_constraint", "diagnostic_clue",
    "verification_method", "applicability", "conclusion",
)


@contextmanager
def _smoke_environment():
    names = (
        "HARDWARE_DATA_ROOT", "HARDWARE_R1_E2E_PROFILE",
        "HARDWARE_R1_E2E_KNOWLEDGE_ENV", "HARDWARE_R1_E2E_KNOWLEDGE_MODE",
        "HARDWARE_KNOWLEDGE_RELEASE_VERSION", "HARDWARE_CASE_MODEL_CONFIG",
        "HARDWARE_CASE_AGENT_CONFIG", "HARDWARE_CASE_RUNTIME_DB",
        "HARDWARE_CASE_API_KEY", "HARDWARE_KNOWLEDGE_BASE_URL",
        "HARDWARE_KNOWLEDGE_READINESS_URL",
    )
    previous = {name: os.environ.get(name) for name in names}
    with tempfile.TemporaryDirectory(prefix="hardware-product-smoke-") as temp:
        os.environ["HARDWARE_DATA_ROOT"] = temp
        os.environ["HARDWARE_R1_E2E_PROFILE"] = "1"
        os.environ["HARDWARE_R1_E2E_KNOWLEDGE_ENV"] = "NON_PROD"
        os.environ["HARDWARE_R1_E2E_KNOWLEDGE_MODE"] = "LOCAL_NON_PROD"
        os.environ["HARDWARE_KNOWLEDGE_RELEASE_VERSION"] = "CI_PACKAGE_SMOKE"
        for name in (
            "HARDWARE_CASE_MODEL_CONFIG", "HARDWARE_CASE_AGENT_CONFIG",
            "HARDWARE_CASE_RUNTIME_DB", "HARDWARE_CASE_API_KEY",
            "HARDWARE_KNOWLEDGE_BASE_URL", "HARDWARE_KNOWLEDGE_READINESS_URL",
        ):
            os.environ.pop(name, None)
        try:
            yield Path(temp)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _synthetic_docx(case_id: str) -> bytes:
    text = f"{case_id} 合成提测：设备启动异常，检查启动链路后恢复正常。"
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>{text}</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with tempfile.TemporaryDirectory(prefix="hardware-smoke-docx-") as temp:
        path = Path(temp) / "A492-Package-Smoke.docx"
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document)
        return path.read_bytes()


def _field(value=None, refs=None, *, status=None, derived=None):
    item = {
        "value": value,
        "status": status or ("EXTRACTED" if value is not None else "MISSING"),
        "evidence_block_ids": list(refs or []),
    }
    if derived is not None:
        item["derived_from_fields"] = list(derived)
    return item


class _SyntheticR1Structurer:
    """Deterministic no-provider responses for package-only product smoke."""

    def __call__(self, runtime_input: dict) -> dict:
        block_id = str(runtime_input["evidence_index"][0]["block_id"])
        return {
            "title": "A492 Package Smoke",
            "product_context": {},
            "facts": {
                "symptom": {
                    "value": "设备启动异常",
                    "evidence_block_ids": [block_id],
                }
            },
            "circuit_feature_links": [],
            "material_links": [],
        }

    def run_stage_a(self, runtime_input: dict, **_kwargs) -> dict:
        block_id = str(runtime_input["evidence_index"][0]["block_id"])
        self._block_id = block_id
        context = {name: _field() for name in CONTEXT_FIELDS}
        context["primary_subject"] = _field("设备", [block_id])
        facts = {name: _field() for name in FACT_FIELDS}
        facts["symptom"] = _field("设备启动异常", [block_id])
        return {
            "ok": True,
            "data": {"engineering_context": {**context, "key_parameters": []}, "facts": facts},
            "runtime": self._runtime("hardware_case.r1_case_extract", "smoke-stage-a"),
        }

    def run_stage_b(self, _runtime_input: dict, **_kwargs) -> dict:
        reusable = {name: _field(derived=[]) for name in REUSABLE_FIELDS}
        reusable["engineering_rule"] = _field(
            "遇到设备启动异常时先核查启动链路。",
            [str(getattr(self, "_block_id", "B0001"))],
            derived=["symptom"],
        )
        return {
            "ok": True,
            "data": {"reusable_knowledge_candidate": reusable},
            "runtime": self._runtime("hardware_case.r1_reuse_derive", "smoke-stage-b"),
        }

    @staticmethod
    def _runtime(agent_id: str, run_id: str) -> dict:
        return {
            "run_id": run_id,
            "task_id": "task-" + run_id,
            "agent_id": agent_id,
            "agent_config_version": "package-smoke-v1",
            "agent_config_hash": "package-smoke-config",
            "prompt_version": "package-smoke-prompt",
            "provider_call_count": 0,
            "provider_call_ms": [],
            "prompt_tokens": "UNKNOWN",
            "completion_tokens": "UNKNOWN",
            "validation_retry_count": 0,
            "retry_budget_exhausted": False,
            "cache_hit": False,
        }


def _require(response, status: int, marker: str) -> dict:
    if response.status_code != status:
        raise SystemExit(f"{marker}_FAIL={response.status_code}:{response.text[:600]}")
    try:
        return response.json()
    except ValueError:
        return {}


def _page(response, marker: str) -> str:
    if response.status_code != 200:
        raise SystemExit(f"{marker}_FAIL={response.status_code}:{response.text[:600]}")
    return response.text


def main() -> int:
    model_config = ROOT / "config/runtime/model.local.yaml"
    if not model_config.is_file():
        raise SystemExit("MODEL_CONFIG_BOOTSTRAP_REQUIRED")

    with _smoke_environment() as data_root:
        # The normal packaged launcher reaches this schema through the hardware
        # startup coordinator.  This in-process product smoke injects the R1
        # structurer directly, so initialize the same durable asset database
        # before composing the app rather than bypassing its migration contract.
        CandidateAssetRepository(
            data_root / "db" / "hardware_asset.db"
        ).initialize()
        structurer = _SyntheticR1Structurer()
        app = create_p0_app(
            data_root / "db/quality.db",
            project_root=ROOT,
            hardware_case_db_path=data_root / "db/hardware_case_mvp.db",
            hardware_tree_upload_dir=data_root / "sources/tree_uploads",
            hardware_case_source_root=data_root / "sources",
            hardware_r1_workbench_db_path=data_root / "db/workbench_runtime.db",
            hardware_r1_preview_db_path=data_root / "rebuildable/preview.db",
            hardware_case_r1_structurer=structurer,
            enabled_domains={"HARDWARE_CASE"},
        )
        with TestClient(app) as client:
            word_page = _page(client.get("/p0/hardware-cases/word-import"), "WORD_IMPORT_ENTRY")
            if "HARDWARE CASE · R1 GOLDEN KNOWLEDGE" not in word_page:
                raise SystemExit("WORD_IMPORT_ENTRY_CONTENT_FAIL")
            production_page = _page(
                client.get("/p0/hardware-cases/knowledge-production"),
                "HUMAN_REVIEW_ENTRY",
            )
            for marker in ("data-promotion-publish", "data-promotion-reconcile", "data-promotion-reconciliation"):
                if marker not in production_page:
                    raise SystemExit("PROMOTION_ENTRY_CONTENT_FAIL=" + marker)

            preview_docx = _synthetic_docx("A0492")
            snapshot_response = client.post(
                "/api/v2/hardware-cases/r1/word-snapshot",
                files={"file": ("A0492-Preview-Smoke.docx", preview_docx, DOCX_MIME)},
                headers=MAINTAINER,
            )
            snapshot = _require(snapshot_response, 200, "WORD_IMPORT_ENTRY")
            preview = _require(
                client.post(
                    "/api/v2/hardware-cases/r1/agent-extract",
                    json=snapshot,
                    headers=MAINTAINER,
                ),
                200,
                "GOLDEN_PREVIEW_ENTRY",
            )
            if (
                preview.get("status") not in {"PASS", "NEEDS_REVIEW"}
                or preview.get("knowledge_object", {}).get("contract_version")
                != "hardware-case-knowledge-object/v1"
                or preview.get("evidence_validation", {}).get("status") != "PASS"
            ):
                raise SystemExit(
                    "GOLDEN_PREVIEW_ENTRY_CONTRACT_FAIL="
                    + str(preview.get("status"))
                    + ":"
                    + str(preview.get("evidence_validation", {}).get("errors"))
                )

            raw_docx = _synthetic_docx("A0493")
            batch = _require(
                client.post(
                    "/api/v2/hardware-cases/r1/workbench/batches",
                    files=[("files", ("A0493-Product-Smoke.docx", raw_docx, DOCX_MIME))],
                    headers=MAINTAINER,
                ),
                201,
                "WORD_IMPORT_ENTRY",
            )
            batch_id = str(batch.get("batch_id") or "")
            if not batch_id:
                raise SystemExit("WORKBENCH_BATCH_ID_MISSING")
            run = _require(
                client.post(
                    f"/api/v2/hardware-cases/r1/workbench/batches/{batch_id}/run-resume",
                    headers=MAINTAINER,
                ),
                200,
                "GOLDEN_PREVIEW_ENTRY",
            )
            items = run.get("items") or []
            if not items or items[0].get("result") not in {"REVIEW", "CANDIDATE_READY"}:
                raise SystemExit("WORKBENCH_PREVIEW_FAIL=" + str(items[:1]))
            item_id = str(items[0]["item_id"])
            candidate_id = str(items[0].get("candidate_id") or "")
            if not candidate_id:
                raise SystemExit("DURABLE_CANDIDATE_ID_MISSING")
            item = _require(
                client.get(
                    f"/api/v2/hardware-cases/r1/workbench/items/{item_id}",
                    headers=MAINTAINER,
                ),
                200,
                "HUMAN_REVIEW_ENTRY",
            )
            knowledge_object = item.get("candidate")
            if not isinstance(knowledge_object, dict):
                raise SystemExit("DURABLE_CANDIDATE_MISSING")
            _require(
                client.post(
                    f"/api/v2/hardware-cases/r1/workbench/items/{item_id}/human-review",
                    json={
                        "decision": "CONFIRM",
                        "reviewer": "PACKAGE_SMOKE",
                        "reason": "Synthetic clean-package smoke approval",
                        "confirmed_content": knowledge_object,
                    },
                    headers=MAINTAINER,
                ),
                200,
                "HUMAN_REVIEW_ENTRY",
            )

            prefix = f"/api/v2/hardware-cases/r1/workbench/items/{item_id}/promotion"
            _require(client.post(prefix + "/intake", headers=MAINTAINER), 200, "PUBLISH_ENTRY")
            _require(
                client.post(
                    prefix + "/review",
                    json={"reviewer": "PACKAGE_SMOKE", "review_comment": "Synthetic package smoke"},
                    headers=MAINTAINER,
                ),
                200,
                "HUMAN_REVIEW_ENTRY",
            )
            published = _require(
                client.post(
                    prefix + "/publish",
                    json={"publisher": "PACKAGE_SMOKE"},
                    headers=MAINTAINER,
                ),
                200,
                "PUBLISH_ENTRY",
            )
            _require(client.post(prefix + "/verify", headers=MAINTAINER), 200, "PUBLISH_ENTRY")
            _require(
                client.post(
                    f"/api/v2/hardware-cases/r1/workbench/consumption/project/{candidate_id}",
                    headers=MAINTAINER,
                ),
                200,
                "KNOWLEDGE_SEARCH_ENTRY",
            )
            reconciliation = _require(
                client.post(prefix + "/reconcile", headers=MAINTAINER),
                200,
                "RECONCILIATION_ENTRY",
            )
            if reconciliation.get("reconciled") is not False:
                raise SystemExit("RECONCILIATION_ENTRY_UNEXPECTED_MUTATION")

            knowledge_id = str(published.get("knowledge_id") or "")
            promotion_view = _require(client.get(prefix, headers=MAINTAINER), 200, "PUBLISH_ENTRY")
            knowledge_id = knowledge_id or str(promotion_view.get("knowledge_id") or "")
            if not knowledge_id:
                raise SystemExit("PUBLISHED_KNOWLEDGE_ID_MISSING")
            search = _require(
                client.get(
                    "/api/public/hardware-knowledge/v1/search",
                    params={"text": "设备启动异常", "limit": 10},
                ),
                200,
                "KNOWLEDGE_SEARCH_ENTRY",
            )
            results = search.get("results") or []
            if not any(str(item.get("knowledge_id")) == knowledge_id for item in results):
                raise SystemExit("KNOWLEDGE_SEARCH_ENTRY_RESULT_MISSING")
            detail = _require(
                client.get(f"/api/public/hardware-knowledge/v1/objects/{knowledge_id}"),
                200,
                "KNOWLEDGE_DETAIL_ENTRY",
            )
            if detail.get("knowledge_id") != knowledge_id:
                raise SystemExit("KNOWLEDGE_DETAIL_ENTRY_BINDING_FAIL")

            for route in (
                "/p0/hardware-cases/search",
                "/p0/hardware-cases/A492-Package-Smoke",
            ):
                if client.get(route).status_code != 200:
                    raise SystemExit("PRODUCT_PAGE_ENTRY_FAIL=" + route)

    print("WORD_IMPORT_ENTRY=PASS")
    print("GOLDEN_PREVIEW_ENTRY=PASS")
    print("HUMAN_REVIEW_ENTRY=PASS")
    print("PUBLISH_ENTRY=PASS")
    print("RECONCILIATION_ENTRY=PASS")
    print("KNOWLEDGE_SEARCH_ENTRY=PASS")
    print("KNOWLEDGE_DETAIL_ENTRY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
