import hashlib
import json
import sqlite3
import shutil
from pathlib import Path
import re

import pytest

from quality_knowledge.p0.initializer import P0InitializationError, P0Initializer
from quality_knowledge.p0.prompt_seed import build_prompt_seeds, build_scoring_seed
from quality_knowledge.p0 import prompt_seed
from quality_knowledge.p0.repository import P0Repository, P0RepositoryError
from quality_knowledge.standard_fields.repository import StandardFieldCatalogError, StandardFieldRepository
from quality_knowledge.standard_fields.service import StandardFieldService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = PROJECT_ROOT / "quality_knowledge" / "config" / "plc_fields.yaml"
MANIFEST_PATH = PROJECT_ROOT / "quality_knowledge" / "config" / "p0_seed_manifest.json"


def make_initializer(manifest_path=MANIFEST_PATH, seed_path=SEED_PATH):
    return P0Initializer(manifest_path=manifest_path, plc_seed_path=seed_path)


@pytest.fixture
def ready_db(tmp_path):
    db_path = tmp_path / "knowledge" / "quality_capability_p0.db"
    result = make_initializer().initialize(db_path)
    return db_path, result


def table_names(db_path):
    with sqlite3.connect(db_path) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_db_01_first_initialization_creates_full_clean_schema(ready_db):
    db_path, result = ready_db
    required = {
        "system_database_metadata", "system_initialization_run", "seed_manifest",
        "product_config", "mapping_config", "mapping_item", "mapping_alias",
        "standard_field_catalog_version", "standard_field_definition", "standard_field_alias",
        "intake_session", "quality_issue", "quality_issue_version", "issue_source_raw",
        "analysis_set", "analysis_stage_run", "issue_analysis_tag", "issue_mrc",
        "issue_capability_gap", "human_analysis_revision", "human_analysis_answer",
    }
    assert result["outcome"] == "APPLIED"
    assert result["initialization_state"] == "READY"
    assert required <= table_names(db_path)


def test_db_02_repeat_is_idempotent_and_keeps_activation_time(ready_db):
    db_path, first = ready_db
    with sqlite3.connect(db_path) as connection:
        activated_at = connection.execute(
            "SELECT activated_at FROM standard_field_catalog_version WHERE status = 'ACTIVE'"
        ).fetchone()[0]
        mapping_count = connection.execute("SELECT COUNT(*) FROM mapping_item").fetchone()[0]
    second = make_initializer().initialize(db_path)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT activated_at FROM standard_field_catalog_version WHERE status = 'ACTIVE'"
        ).fetchone()[0] == activated_at
        assert connection.execute("SELECT COUNT(*) FROM mapping_item").fetchone()[0] == mapping_count
    assert second["outcome"] == "ALREADY_APPLIED"
    assert first["database_instance_id"] == second["database_instance_id"]


def test_db_03_same_seed_version_with_different_hash_is_blocked(ready_db, tmp_path):
    db_path, _ = ready_db
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["seeds"][0]["sha256"] = "0" * 64
    changed_manifest = tmp_path / "changed_manifest.json"
    changed_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(P0InitializationError, match="SEED_MANIFEST_HASH_CONFLICT"):
        make_initializer(changed_manifest).initialize(db_path)


def test_db_03_technical_column_exclusion_participates_in_manifest_idempotency_hash(ready_db, tmp_path):
    db_path, _ = ready_db
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["technical_field_exclusions"]["PRODUCT_EXTENSION.source_id"]["handling"] = "IGNORED"
    changed_manifest = tmp_path / "changed_technical_column_manifest.json"
    changed_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(P0InitializationError, match="SEED_MANIFEST_HASH_CONFLICT"):
        make_initializer(changed_manifest).initialize(db_path)


def test_db_04_legacy_file_is_only_copied_and_left_untouched(tmp_path):
    legacy = tmp_path / "legacy_validation.db"
    legacy.write_bytes(b"not-an-openable-sqlite-database")
    old_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
    old_stat = legacy.stat()
    result = make_initializer().initialize(
        tmp_path / "new" / "p0.db",
        legacy_backup_source=legacy,
        backup_directory=tmp_path / "backup",
    )
    assert result["backup"]["status"] == "VERIFIED"
    assert hashlib.sha256(legacy.read_bytes()).hexdigest() == old_hash
    assert legacy.stat().st_mtime_ns == old_stat.st_mtime_ns
    assert Path(result["backup"]["backup_path"]).read_bytes() == legacy.read_bytes()


def test_db_05_missing_legacy_file_is_not_a_blocker(tmp_path):
    result = make_initializer().initialize(tmp_path / "new" / "p0.db", legacy_backup_source=tmp_path / "missing.db")
    assert result["backup"]["status"] == "NOT_APPLICABLE"


def test_db_06_interrupted_initialization_can_resume_safely(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE system_database_metadata SET initialization_state = 'INITIALIZATION_BLOCKED'")
        connection.commit()
    result = make_initializer().initialize(db_path)
    assert result["outcome"] == "APPLIED"
    assert result["initialization_state"] == "READY"


def test_db_07_repository_does_not_create_schema_or_alter(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(P0RepositoryError, match="P0_DATABASE_NOT_INITIALIZED"):
        P0Repository(missing)
    assert not missing.exists()


def test_db_08_integrity_and_foreign_keys_are_clean(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM mapping_config WHERE status = 'ACTIVE'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM standard_field_catalog_version WHERE status = 'ACTIVE'").fetchone()[0] == 1


def test_db_09_active_prompt_and_scoring_seeds_are_real_and_frozen(ready_db):
    db_path, _ = ready_db
    expected_prompts = {item["stage"]: item for item in build_prompt_seeds()}
    expected_scoring = build_scoring_seed()
    with sqlite3.connect(db_path) as connection:
        prompts = connection.execute(
            "SELECT stage, prompt_text, content_hash, output_contract_json, model_params_json FROM analysis_prompt_version WHERE status = 'ACTIVE'"
        ).fetchall()
        scoring = connection.execute(
            "SELECT scoring_version_id, weights_json, content_hash FROM insight_scoring_version WHERE status = 'ACTIVE'"
        ).fetchone()
    assert len(prompts) == 4
    for stage, text, content_hash, output_contract, model_params in prompts:
        assert content_hash == expected_prompts[stage]["content_hash"]
        assert text == expected_prompts[stage]["prompt_text"]
        assert json.loads(output_contract)["contract_version"] == "2.0.0"
        assert json.loads(model_params)["response_format"] == "json_object"
    assert scoring[0] == expected_scoring["scoring_version_id"]
    assert scoring[2] == expected_scoring["content_hash"]
    assert set(json.loads(scoring[1])) == set(expected_scoring["weights"])


def test_db_10_prompt_database_tamper_blocks_ready_verification(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE analysis_prompt_version SET prompt_text = 'tampered' WHERE stage = 'occurrence'")
        connection.commit()
    with pytest.raises(P0InitializationError, match="P0_ACTIVE_PROMPT_CONTENT_INVALID"):
        make_initializer().verify_ready(db_path)


def test_db_11_prompt_release_artifact_tamper_blocks_initialization(tmp_path, monkeypatch):
    copied_prompts = tmp_path / "prompts"
    shutil.copytree(PROJECT_ROOT / "quality_knowledge" / "prompts_v2", copied_prompts)
    occurrence = copied_prompts / "occurrence_v2.md"
    occurrence.write_text(occurrence.read_text(encoding="utf-8") + "\n篡改", encoding="utf-8")
    monkeypatch.setattr(prompt_seed, "PROMPT_DIRECTORY", copied_prompts)
    with pytest.raises(P0InitializationError, match="PROMPT_SET_P0_V1_HASH_MISMATCH"):
        make_initializer().initialize(tmp_path / "tampered-prompt.db")


def test_fc_01_plc_seed_has_59_business_fields_111_seed_aliases_and_one_required(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        fields = connection.execute("SELECT COUNT(*) FROM standard_field_definition WHERE enabled = 1").fetchone()[0]
        aliases = connection.execute("SELECT COUNT(*) FROM mapping_alias").fetchone()[0]
        required = connection.execute(
            "SELECT target_domain || '.' || target_field FROM standard_field_definition WHERE required = 1"
        ).fetchall()
    assert fields == 59
    assert aliases == 111
    assert [row[0] for row in required] == ["ISSUE_FACT.business_issue_id"]


def test_fc_01_active_business_fields_have_chinese_labels_and_source_id_is_excluded(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        labels = connection.execute(
            "SELECT label_zh FROM standard_field_definition WHERE enabled = 1 ORDER BY display_order"
        ).fetchall()
    assert len(labels) == 59
    assert all(re.search(r"[\u4e00-\u9fff]", row[0]) for row in labels)
    repository = StandardFieldRepository(db_path)
    for query in ("来源ID", "source_id", "PRODUCT_EXTENSION.source_id"):
        assert repository.search_active(query) == []
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            """SELECT COUNT(*) FROM standard_field_definition
                 WHERE target_domain = 'PRODUCT_EXTENSION' AND target_field = 'source_id'"""
        ).fetchone()[0] == 0


def test_fc_01_source_id_is_a_known_raw_only_technical_column(ready_db):
    db_path, _ = ready_db
    service = StandardFieldService(
        StandardFieldRepository(db_path),
        SEED_PATH,
        technical_field_exclusions={"PRODUCT_EXTENSION.source_id": {"handling": "RAW_ONLY"}},
    )
    with sqlite3.connect(db_path) as connection:
        resolution = service.resolve_source_header("SourceID", connection)
        record = connection.execute(
            "SELECT details_json FROM mapping_validation_result WHERE code = 'KNOWN_TECHNICAL_COLUMN'"
        ).fetchone()
    assert resolution == {
        "source_header": "SourceID",
        "normalized_header": "SourceID",
        "status": "IGNORED",
        "target": None,
        "required": False,
        "candidates": [],
        "can_confirm": True,
        "handling_requirement": "RAW_ONLY",
    }
    assert json.loads(record[0])["field_path"] == "PRODUCT_EXTENSION.source_id"
    assert json.loads(record[0])["handling"] == "RAW_ONLY"


def test_fc_02_missing_or_wrong_plc_seed_is_blocked(tmp_path):
    with pytest.raises(P0InitializationError, match="PLC_SEED_HASH_MISMATCH"):
        make_initializer(seed_path=tmp_path / "missing.yaml").initialize(tmp_path / "p0.db")


def test_fc_03_active_mapping_or_catalog_absence_blocks_ready_check(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE mapping_config SET status = 'RETIRED' WHERE business_type = 'PLC'")
        connection.commit()
    with pytest.raises(P0InitializationError, match="PLC_ACTIVE_MAPPING_OR_CATALOG_MISSING"):
        make_initializer().verify_ready(db_path)


def test_fc_04_mapping_and_catalog_field_mismatch_blocks_ready_check(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE mapping_item SET enabled = 0 WHERE target_field = 'month'")
        connection.commit()
    with pytest.raises(P0InitializationError, match="PLC_MAPPING_CATALOG_FIELDSET_MISMATCH"):
        make_initializer().verify_ready(db_path)


def test_fc_05_catalog_alias_constraint_blocks_ambiguous_aliases(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        catalog_id = connection.execute("SELECT catalog_version_id FROM standard_field_catalog_version").fetchone()[0]
        field_id = connection.execute("SELECT field_definition_id FROM standard_field_definition LIMIT 1").fetchone()[0]
        alias = connection.execute("SELECT alias, normalized_alias FROM standard_field_alias LIMIT 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO standard_field_alias(field_alias_id, catalog_version_id, field_definition_id, alias, normalized_alias, alias_type)
                   VALUES ('conflict', ?, ?, ?, ?, 'TEST')""",
                (catalog_id, field_id, alias[0] + "2", alias[1]),
            )


def test_fc_05_ambiguous_seed_alias_is_explicit_and_requires_preview_disambiguation(ready_db):
    db_path, _ = ready_db
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT details_json FROM mapping_validation_result WHERE code = 'AMBIGUOUS_SEED_ALIAS'"
        ).fetchone()
        service = StandardFieldService(
            StandardFieldRepository(db_path),
            SEED_PATH,
            technical_field_exclusions={"PRODUCT_EXTENSION.source_id": {"handling": "RAW_ONLY"}},
        )
        resolved = service.resolve_source_header("变更影响", connection)
    assert json.loads(row[0]) == {
        "candidates": ["ISSUE_FACT.impact", "PRODUCT_EXTENSION.change_impact"],
        "conflict_type": "AMBIGUOUS_SEED_ALIAS",
        "handling_requirement": "REQUIRES_PREVIEW_DISAMBIGUATION",
        "normalized_alias": "变更影响",
        "original_alias": "变更影响",
    }
    assert resolved["status"] == "CONFLICT"
    assert [candidate["path"] for candidate in resolved["candidates"]] == [
        "ISSUE_FACT.impact", "PRODUCT_EXTENSION.change_impact"
    ]
    assert resolved["can_confirm"] is False
    assert resolved["handling_requirement"] == "REQUIRES_PREVIEW_DISAMBIGUATION"


def test_fc_05_source_header_resolver_has_deterministic_zero_one_many_outcomes(ready_db):
    db_path, _ = ready_db
    service = StandardFieldService(
        StandardFieldRepository(db_path),
        SEED_PATH,
        technical_field_exclusions={"PRODUCT_EXTENSION.source_id": {"handling": "RAW_ONLY"}},
    )
    with sqlite3.connect(db_path) as connection:
        unmatched = service.resolve_source_header("不存在的字段", connection)
        matched = service.resolve_source_header("问题描述", connection)
        conflict = service.resolve_source_header("变更影响", connection)
    assert unmatched["status"] == "UNMATCHED" and unmatched["candidates"] == []
    assert matched["status"] == "MATCHED" and matched["candidates"][0]["path"] == "ISSUE_FACT.description"
    assert conflict["status"] == "CONFLICT" and len(conflict["candidates"]) == 2


def test_fc_06_new_product_starter_uses_all_active_catalog_targets(ready_db):
    db_path, _ = ready_db
    draft = StandardFieldRepository(db_path).create_product_starter_draft("robot")
    assert draft["product_code"] == "ROBOT"
    assert len(draft["targets"]) == 59
    assert all(not item["source_headers"] for item in draft["targets"])


def test_fc_06_catalog_search_supports_chinese_key_path_and_alias(ready_db):
    db_path, _ = ready_db
    repository = StandardFieldRepository(db_path)
    assert repository.search_active("问题描述")[0].target_field == "description"
    assert repository.search_active("description")[0].target_field == "description"
    assert repository.search_active("ISSUE_FACT.description")[0].target_field == "description"


def test_fc_07_controlled_addition_creates_next_draft_then_activates(ready_db):
    db_path, _ = ready_db
    repository = StandardFieldRepository(db_path)
    with repository.connect() as connection:
        connection.execute("BEGIN")
        draft = repository.create_controlled_draft(
            proposed_domain="PRODUCT_EXTENSION",
            proposed_field="robot_payload",
            label_zh="机器人负载",
            definition_zh="机器人负载能力范围",
            data_type="string",
            business_example="机器人规格",
            reuse_rationale="现有目录没有等价负载字段",
            candidate_fields=[],
            source_context={"product": "ROBOT", "header": "负载"},
            requested_by="tester",
            connection=connection,
        )
        connection.commit()
    repository.approve_change_request(draft["change_request_id"], "approver")
    activated = repository.activate_catalog(draft["catalog_version_id"], "approver")
    assert activated["status"] == "ACTIVE"
    assert len(repository.search_active("机器人负载")) == 1


def test_fc_08_direct_unknown_target_cannot_be_selected(ready_db):
    db_path, _ = ready_db
    repository = StandardFieldRepository(db_path)
    with repository.connect() as connection:
        connection.execute("BEGIN")
        with pytest.raises(StandardFieldCatalogError, match="STANDARD_FIELD_KEY_INVALID"):
            repository.create_controlled_draft(
                proposed_domain="ISSUE_FACT",
                proposed_field="Arbitrary Key",
                label_zh="任意字段",
                definition_zh="不应直接创建",
                data_type="string",
                business_example="x",
                reuse_rationale="x",
                candidate_fields=[],
                source_context={},
                requested_by="tester",
                connection=connection,
            )
        connection.rollback()
