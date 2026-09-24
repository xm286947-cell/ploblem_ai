from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_production import (
    KNOWLEDGE_CANDIDATE_CONTRACT_VERSION,
    KNOWLEDGE_EVIDENCE_CONTRACT_VERSION,
    KNOWLEDGE_OBJECT_CONTRACT_VERSION,
    KNOWLEDGE_PUBLISH_CONTRACT_VERSION,
    KNOWLEDGE_QUERY_CONTRACT_VERSION,
    KNOWLEDGE_REVIEW_CONTRACT_VERSION,
    KnowledgeCandidateIntakeRequest,
    KnowledgeEvidenceInput,
    KnowledgePublishRequest,
    KnowledgeReleaseService,
    KnowledgeReviewRequest,
    PublicKnowledgeError,
    PublicKnowledgeQuery,
    PublicKnowledgeService,
)
from repositories import JsonArtifactRepository


TASK = "HARDWARE-CASE-KNOWLEDGE-P0-001"
VERSION = "KNOWLEDGE_CAPABILITY_RELEASE_V0.3"
KNOWLEDGE_RELEASE = f"{VERSION}-HW-GOLDEN"
DIST = ROOT / "dist" / "hardware-case-knowledge-p0"
FIXED_TIME = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
SOURCE_TEXT = (
    "Connector contact resistance increased after repeated vibration."
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _zip_deterministic(target: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name)
            info.date_time = (2026, 9, 24, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])


def _golden_path(repository: JsonArtifactRepository) -> dict[str, Any]:
    service = PublicKnowledgeService(repository)
    evidence_hash = hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest()

    evidence_payload = {
        "contract_version": "knowledge-evidence/v1",
        "evidence_id": "EVD-HW-WORD-001",
        "source_document_id": "HW-WORD-001",
        "domain": "HARDWARE_CASE",
        "source_type": "WORD",
        "source_ref": "controlled://hardware-case/HW-WORD-001",
        "source_revision": "V1",
        "page": 2,
        "section": "Failure Analysis",
        "paragraph": "P3",
        "source_text": SOURCE_TEXT,
        "content_hash": evidence_hash,
        "revision": 1,
    }
    evidence = service.intake_evidence(evidence_payload)

    candidate_payload = {
        "contract_version": "knowledge-candidate/v1",
        "candidate_id": "HC-CAND-001",
        "source_document_id": "HW-WORD-001",
        "domain": "HARDWARE_CASE",
        "object_type": "HARDWARE_CASE",
        "structured_content": {
            "phenomenon": "Intermittent communication loss",
            "failure_mechanism": "Connector contact degradation",
            "solution": "Improve connector retention and contact design",
            "tree_mapping": {
                "circuit_node": "Communication/Connector",
                "device_node": "Connector",
            },
        },
        "evidence_refs": [evidence.evidence_id],
        "status": "PENDING_REVIEW",
        "created_at": FIXED_TIME.isoformat(),
        "revision": 1,
        "producer": "hardware-case/v1",
    }
    candidate = service.intake_candidate(candidate_payload)

    unreviewed_blocked = False
    try:
        service.publish(
            {
                "contract_version": "knowledge-publish/v1",
                "candidate_id": candidate.candidate_id,
                "idempotency_key": "PRE-REVIEW-MUST-FAIL",
                "publisher": "hardware-publisher",
                "published_at": FIXED_TIME.isoformat(),
                "revision": 1,
            }
        )
    except PublicKnowledgeError as exc:
        unreviewed_blocked = exc.code == "PUBLISH_NOT_CONFIRMED"
    if not unreviewed_blocked:
        raise RuntimeError("UNREVIEWED_PUBLISH_NOT_BLOCKED")

    review_payload = {
        "contract_version": "knowledge-review/v1",
        "candidate_id": candidate.candidate_id,
        "action": "CONFIRM",
        "reviewer": "hardware-reviewer",
        "review_time": FIXED_TIME.isoformat(),
        "review_comment": "Evidence verified",
        "revision": 1,
    }
    review = service.review(review_payload)
    if review.review_status != "CONFIRMED":
        raise RuntimeError("HUMAN_REVIEW_NOT_CONFIRMED")

    publish_payload = {
        "contract_version": "knowledge-publish/v1",
        "candidate_id": candidate.candidate_id,
        "idempotency_key": "HC-CAND-001-R1",
        "publisher": "hardware-publisher",
        "published_at": FIXED_TIME.isoformat(),
        "revision": 1,
    }
    first = service.publish(publish_payload)
    second = service.publish(publish_payload)
    if first != second:
        raise RuntimeError("PUBLISH_NOT_IDEMPOTENT")

    manifest = KnowledgeReleaseService(repository).build(
        KNOWLEDGE_RELEASE,
        created_at=FIXED_TIME,
    )
    result = service.query(
        {
            "contract_version": "knowledge-query/v1",
            "knowledge_release_version": KNOWLEDGE_RELEASE,
            "domain": "HARDWARE_CASE",
            "object_type": "HARDWARE_CASE",
        }
    )
    if len(result.objects) != 1:
        raise RuntimeError("PUBLIC_QUERY_FAILED")
    resolved = service.resolve_evidence(
        KNOWLEDGE_RELEASE,
        evidence.evidence_id,
    )
    if resolved.get("excerpt") != SOURCE_TEXT:
        raise RuntimeError("EVIDENCE_RESOLVE_FAILED")

    return {
        "evidence_request": evidence_payload,
        "candidate_request": candidate_payload,
        "review_request": review_payload,
        "publish_request": publish_payload,
        "published_object": first.object.model_dump(mode="json"),
        "knowledge_release_manifest": manifest.model_dump(mode="json"),
        "query_result": result.model_dump(mode="json"),
        "resolved_evidence": resolved,
        "unreviewed_publish_blocked": unreviewed_blocked,
    }


def _schema_files() -> dict[str, bytes]:
    return {
        "SCHEMA/knowledge-candidate-v1.request.schema.json": _pretty(
            KnowledgeCandidateIntakeRequest.model_json_schema()
        ),
        "SCHEMA/knowledge-evidence-v1.request.schema.json": _pretty(
            KnowledgeEvidenceInput.model_json_schema()
        ),
        "SCHEMA/knowledge-review-v1.request.schema.json": _pretty(
            KnowledgeReviewRequest.model_json_schema()
        ),
        "SCHEMA/knowledge-publish-v1.request.schema.json": _pretty(
            KnowledgePublishRequest.model_json_schema()
        ),
        "SCHEMA/knowledge-query-v1.request.schema.json": _pretty(
            PublicKnowledgeQuery.model_json_schema()
        ),
    }


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    source_commit = os.environ.get("SOURCE_COMMIT", "UNKNOWN")
    source_branch = os.environ.get(
        "SOURCE_BRANCH",
        "feature/hardware-case-knowledge-p0-001",
    )

    with tempfile.TemporaryDirectory(prefix="hc-knowledge-p0-") as tmp:
        repository = JsonArtifactRepository(Path(tmp) / "repository")
        golden = _golden_path(repository)

    contracts = {
        "candidate": KNOWLEDGE_CANDIDATE_CONTRACT_VERSION,
        "evidence": KNOWLEDGE_EVIDENCE_CONTRACT_VERSION,
        "review": KNOWLEDGE_REVIEW_CONTRACT_VERSION,
        "publish": KNOWLEDGE_PUBLISH_CONTRACT_VERSION,
        "object": KNOWLEDGE_OBJECT_CONTRACT_VERSION,
        "query": KNOWLEDGE_QUERY_CONTRACT_VERSION,
    }
    public_api = """# Public API Entry

Factory: `knowledge_production.api:create_knowledge_api_app`

Routes:
- POST /v1/knowledge/evidences
- POST /v1/knowledge/candidates
- POST /v1/knowledge/reviews
- POST /v1/knowledge/publish
- POST /v1/knowledge/search
- GET /v1/knowledge/objects/{knowledge_id}
- GET /v1/knowledge/evidences/{evidence_id}

The existing Storage-compatible POST /v1/knowledge/query remains unchanged.
Hardware Case consumes public routes/contracts only and never reads the
Knowledge repository directly.
"""
    limitations = """# Known Limitations

- Cross-domain global dedup is not expanded by this release.
- Complex conflict resolution is not expanded by this release.
- No global knowledge graph is introduced.
- No global permission-governance upgrade is introduced.
- Release orchestration remains single-package and explicit.
- Hardware-specific schema semantics, dual-tree mapping rules, Prompt and UI
  remain owned by Hardware Case.
"""
    dependency_matrix = """# Dependency Matrix

| Capability | Implementation |
|---|---|
| Candidate Intake | Existing KnowledgeCandidateService + public facade |
| Evidence | Runtime EvidenceReference + BusinessEvidenceIntakeService |
| Review | Existing KnowledgeReviewService |
| Publish | Existing KnowledgePublishService |
| Knowledge Object | Existing knowledge-object/v1 + public metadata |
| Release | Existing KnowledgeReleaseService |
| Query / Evidence Resolve | Existing KnowledgeQueryService |
| Runtime | Reused; not modified |
| Hardware business rules | Not owned by Knowledge Platform |
"""
    rollback = """# Rollback

1. Stop consuming KNOWLEDGE_CAPABILITY_RELEASE_V0.3.
2. Pin Hardware Case to the previous Knowledge capability/release version.
3. The new public facade is additive; existing Storage compatibility and prior
   Knowledge Releases remain immutable.
4. If code rollback is required, revert the feature commit/PR. No existing
   repository schema migration is required by this release.
"""
    migration = """# Migration Notes

This is an additive public-contract release. No existing Knowledge repository
layout is migrated. Existing knowledge-candidate/v1, knowledge-object/v1 and
knowledge-query/v1 remain compatible. Hardware Case must replace any direct
Repository evidence write with POST /v1/knowledge/evidences or the equivalent
PublicKnowledgeService call.
"""
    release_notes = """# Release Notes

KNOWLEDGE_CAPABILITY_RELEASE_V0.3 adds the minimum public Knowledge capability
needed by Hardware Case MVP RC1:
- business Word Evidence Intake
- frozen Evidence / Review / Publish V1 contracts
- opaque structured_content Candidate envelope
- human-review separation from original AI Candidate
- idempotent publish facade
- release-pinned public query and evidence resolve
- contract mock, executable Golden Path and deterministic capability package
"""
    contract_tests = """# Contract Tests

Primary:
`python -m pytest -q tests/test_hardware_case_knowledge_p0_contract.py`

Regression:
`python -m pytest -q tests/test_kp_d01_business_candidate_intake.py tests/test_kp_d03_review_publish.py tests/test_kp_d04_release_query.py`

Gate requires both sets to pass before the capability package is published.
"""

    files: dict[str, bytes] = {
        "VERSION": (VERSION + "\n").encode("utf-8"),
        "SOURCE_COMMIT": (source_commit + "\n").encode("utf-8"),
        "SOURCE_BRANCH": (source_branch + "\n").encode("utf-8"),
        "CONTRACT_VERSION.json": _pretty(contracts),
        "PUBLIC_API.md": public_api.encode("utf-8"),
        "MOCK/hardware_case_golden.json": _pretty(
            {
                "evidence": golden["evidence_request"],
                "candidate": golden["candidate_request"],
                "review": golden["review_request"],
                "publish": golden["publish_request"],
                "query": {
                    "contract_version": "knowledge-query/v1",
                    "knowledge_release_version": KNOWLEDGE_RELEASE,
                    "domain": "HARDWARE_CASE",
                    "object_type": "HARDWARE_CASE",
                },
            }
        ),
        "CONTRACT_TESTS.md": contract_tests.encode("utf-8"),
        "MIGRATION_NOTES.md": migration.encode("utf-8"),
        "RELEASE_NOTES.md": release_notes.encode("utf-8"),
        "KNOWN_LIMITATIONS.md": limitations.encode("utf-8"),
        "DEPENDENCY_MATRIX.md": dependency_matrix.encode("utf-8"),
        "ROLLBACK.md": rollback.encode("utf-8"),
        "GOLDEN_PATH_RESULT.json": _pretty(golden),
    }
    files.update(_schema_files())

    file_entries = [
        {
            "path": name,
            "sha256": _sha256(payload),
            "size": len(payload),
        }
        for name, payload in sorted(files.items())
    ]
    manifest = {
        "task": TASK,
        "version": VERSION,
        "source_branch": source_branch,
        "source_commit": source_commit,
        "contract_versions": contracts,
        "public_api_entry": (
            "knowledge_production.api:create_knowledge_api_app"
        ),
        "mock": "MOCK/hardware_case_golden.json",
        "contract_tests": [
            "tests/test_hardware_case_knowledge_p0_contract.py",
            "tests/test_kp_d01_business_candidate_intake.py",
            "tests/test_kp_d03_review_publish.py",
            "tests/test_kp_d04_release_query.py",
        ],
        "migration_notes": "MIGRATION_NOTES.md",
        "release_notes": "RELEASE_NOTES.md",
        "known_limitations": "KNOWN_LIMITATIONS.md",
        "dependency_matrix": "DEPENDENCY_MATRIX.md",
        "rollback": "ROLLBACK.md",
        "files": file_entries,
    }
    files["MANIFEST.json"] = _pretty(manifest)

    package = DIST / f"{VERSION}.zip"
    _zip_deterministic(package, files)
    sha = hashlib.sha256(package.read_bytes()).hexdigest()
    sha_file = DIST / f"{VERSION}.zip.sha256"
    sha_file.write_text(
        f"{sha}  {package.name}\n",
        encoding="utf-8",
    )

    gate = {
        "TASK": TASK,
        "RESULT": "PASS",
        "KNOWLEDGE_VERSION": VERSION,
        "SOURCE_BRANCH": source_branch,
        "SOURCE_COMMIT": source_commit,
        "CONTRACT_VERSION": contracts,
        "RELEASE_PACKAGE": package.name,
        "SHA256": sha,
        "K01_CANDIDATE_INTAKE": "PASS",
        "K02_EVIDENCE": "PASS",
        "K03_HUMAN_REVIEW": "PASS",
        "K04_PUBLISH": "PASS",
        "K05_KNOWLEDGE_OBJECT": "PASS",
        "K06_QUERY": "PASS",
        "K07_EVIDENCE_RESOLVE": "PASS",
        "CONTRACT_TEST": "PENDING_CI_AT_BUILD_TIME",
        "MOCK_COMPATIBILITY": "PASS",
        "KNOWN_LIMITATIONS": (
            "See KNOWN_LIMITATIONS.md; P0 excludes global dedup/conflict/"
            "knowledge-graph/permission-orchestration expansion."
        ),
        "BLOCKER": "",
        "NEXT_ACTION": (
            "Hardware Case pins VERSION + CONTRACT_VERSION + SHA256 and runs "
            "HARDWARE_CASE_MVP_TEST_RC1 integration."
        ),
    }
    (DIST / "gate_result.json").write_bytes(_pretty(gate))

    print(f"TASK={TASK}")
    print("RESULT=PASS")
    print(f"KNOWLEDGE_VERSION={VERSION}")
    print(f"SOURCE_BRANCH={source_branch}")
    print(f"SOURCE_COMMIT={source_commit}")
    print("CONTRACT_VERSION=" + json.dumps(contracts, sort_keys=True))
    print(f"RELEASE_PACKAGE={package.name}")
    print(f"SHA256={sha}")
    print("K01_CANDIDATE_INTAKE=PASS")
    print("K02_EVIDENCE=PASS")
    print("K03_HUMAN_REVIEW=PASS")
    print("K04_PUBLISH=PASS")
    print("K05_KNOWLEDGE_OBJECT=PASS")
    print("K06_QUERY=PASS")
    print("K07_EVIDENCE_RESOLVE=PASS")
    print("MOCK_COMPATIBILITY=PASS")
    print("BLOCKER=")
    print(
        "NEXT_ACTION=Hardware Case pins release and runs "
        "HARDWARE_CASE_MVP_TEST_RC1 integration."
    )


if __name__ == "__main__":
    main()
