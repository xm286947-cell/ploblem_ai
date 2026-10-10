"""QS #601: a real-source run cannot silently use the controlled Mock."""
from pathlib import Path
import sqlite3
import pytest
from tools.run_quality_scenario_windows_acceptance import (
    require_real_provider_config, safe_sqlite_snapshot,
)


def test_real_provider_fails_closed_on_missing_auth_and_mock(tmp_path, monkeypatch):
    cfg = tmp_path / "model.yaml"
    cfg.write_text(
        "active_model: qwen_prod\nmodels:\n  qwen_prod:\n"
        "    provider: openai_compatible\n    model: real-model\n"
        "    base_url_env: QS_601_URL\n    api_key_env: QS_601_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("QS_601_URL", raising=False)
    monkeypatch.delenv("QS_601_KEY", raising=False)
    with pytest.raises(ValueError, match="CREDENTIAL_ENV_MISSING"):
        require_real_provider_config(cfg)
    monkeypatch.setenv("QS_601_KEY", "private-test-key")
    with pytest.raises(ValueError, match="ENDPOINT_ENV_MISSING"):
        require_real_provider_config(cfg)
    monkeypatch.setenv("QS_601_URL", "https://real.example.test/v1")
    assert require_real_provider_config(cfg) == cfg.resolve()
    cfg.write_text(
        "active_model: x\nmodels:\n  x:\n"
        "    provider: openai_compatible\n    model: mock-gpt\n"
        "    base_url: http://127.0.0.1:18090/v1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="REFUSE_CONTROLLED_MOCK"):
        require_real_provider_config(cfg)


def test_sqlite_wal_backup_is_consistent_and_original_untouched(tmp_path):
    src, dst = tmp_path / "source.db", tmp_path / "snapshot.db"
    writer = sqlite3.connect(src)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE facts(k INTEGER PRIMARY KEY, v TEXT)")
        writer.commit()
        writer.execute("INSERT INTO facts VALUES(1, ?)", ("business-original",))
        writer.commit()
        safe_sqlite_snapshot(src, dst)
        with sqlite3.connect(dst) as c:
            assert c.execute("SELECT v FROM facts").fetchone() == ("business-original",)
            c.execute("INSERT INTO facts VALUES (2, 'isolated')")
        assert writer.execute("SELECT count(*) FROM facts").fetchone()[0] == 1
    finally:
        writer.close()


def test_real_windows_entry_has_explicit_flag_and_durable_storage():
    root = Path(__file__).resolve().parents[1]
    bat = (root / "start_quality_scenario_windows_real_provider.bat").read_text(encoding="utf-8")
    runner = (root / "tools/run_quality_scenario_windows_acceptance.py").read_text(encoding="utf-8")
    assert "--real-provider" in bat and "--source-db" in bat
    assert "18090" not in bat
    assert "if not args.real_provider:" in runner
    assert "quality_scenario_v1_real_" in runner
