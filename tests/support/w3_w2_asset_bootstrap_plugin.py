"""Test-only W2 bootstrap compatibility fixture for two legacy Word API cases.

Uses public existing product CandidateAssetRepository.initialize() migrations.
Only two allowlisted historical tests. No exception swallowing, assertion
patching, HTTP interception, Agent/Provider mock rewrite or product code change.
"""
from __future__ import annotations
import pytest
from services.hardware_asset_repository import CandidateAssetRepository

_ALLOW={
 "test_hardware_r1_word_import_web.py::test_docx_upload_renders_frozen_snapshot_contract",
 "test_hardware_r1_word_import_web.py::test_r1_agent_poc_uses_injected_unified_runtime_and_evidence_gate",
}
@pytest.fixture(autouse=True)
def w2_initialize_real_asset_journal_before_legacy_word_tests(request):
    node=request.node.nodeid.replace("\\","/")
    if not any(node.endswith(s) for s in _ALLOW):
        return
    root=request.getfixturevalue("tmp_path")
    real=CandidateAssetRepository(root/"hardware_asset.db")
    migration=real.initialize()
    print("W2_TEST_FIXTURE_ASSET_SCHEMA_READY="+node)
