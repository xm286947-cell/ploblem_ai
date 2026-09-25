from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_EVIDENCE_KEYS = [
    "resolved_model",
    "model_ref",
    "agent_config_source",
    "model_config_source",
    "config_hash",
    "resolved_max_tokens",
    "request_max_tokens",
    "request_max_completion_tokens",
    "structured_output_capability",
    "structured_output_capability_source",
    "structured_output_modes",
    "structured_output_request",
    "response_format_type",
    "structured_output_fallback",
    "raw_usage",
    "raw_finish_reason",
    "content_length",
    "content_hash",
    "streaming",
    "chunk_diagnostics",
    "chunk_count",
    "chunk_sequence_verification",
    "aggregate_length",
    "aggregate_hash",
    "recovered",
    "recovery_type",
    "recovery_classification",
    "original_content_hash",
    "recovered_content_hash",
    "strict_parse_status",
    "strict_parse_after_recovery",
    "schema_validation_status",
    "schema_validation_after_recovery",
    "token_usage_contract_anomaly",
    "response_http_status",
    "provider_request_id",
]


def _default_db() -> Path | None:
    preferred = ROOT / ".testdata" / "real-normal-runtime.sqlite3"
    if preferred.is_file():
        return preferred
    candidates = sorted(
        (ROOT / ".testdata").glob("*-runtime.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if (ROOT / ".testdata").exists() else []
    return candidates[0] if candidates else None


def _read_latest(db: Path) -> dict[str, Any]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT attempt_id, status, provider_call_seq, record_json
            FROM runtime_attempt
            ORDER BY rowid DESC
            """
        ).fetchall()
    for row in rows:
        try:
            record = json.loads(row["record_json"])
        except Exception:
            continue
        metrics = record.get("execution_metrics") or {}
        evidence = metrics.get("provider_evidence") or {}
        if not isinstance(evidence, dict) or not evidence:
            continue
        safe = {key: evidence.get(key, "NOT_EVIDENCED") for key in ALLOWED_EVIDENCE_KEYS}
        return {
            "runtime_db": str(db.resolve()),
            "attempt_id": row["attempt_id"],
            "attempt_status": row["status"],
            "provider_call_seq": row["provider_call_seq"],
            "provider_evidence": safe,
        }
    raise RuntimeError("NO_PROVIDER_EVIDENCE_FOUND")


def _write_outputs(payload: dict[str, Any]) -> tuple[Path, Path]:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    json_path = logs / "runtime_provider_evidence_latest.json"
    txt_path = logs / "runtime_provider_evidence_latest.txt"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    e = payload["provider_evidence"]
    lines = [
        f"RESOLVED_MODEL={e.get('resolved_model')}",
        f"MODEL_REF={e.get('model_ref')}",
        f"MODEL_CONFIG_SOURCE={e.get('model_config_source')}",
        f"CONFIG_HASH={e.get('config_hash')}",
        f"RESOLVED_MAX_TOKENS={e.get('resolved_max_tokens')}",
        f"REQUEST_MAX_TOKENS={e.get('request_max_tokens')}",
        f"REQUEST_MAX_COMPLETION_TOKENS={e.get('request_max_completion_tokens')}",
        "RAW_USAGE=" + json.dumps(e.get("raw_usage"), ensure_ascii=False, separators=(",", ":")),
        f"RAW_FINISH_REASON={e.get('raw_finish_reason')}",
        f"STRUCTURED_OUTPUT_CAPABILITY={e.get('structured_output_capability')}",
        f"STRUCTURED_OUTPUT_REQUEST={e.get('structured_output_request')}",
        f"RESPONSE_FORMAT_TYPE={e.get('response_format_type')}",
        f"RECOVERED={e.get('recovered')}",
        f"RECOVERY_TYPE={e.get('recovery_type')}",
        f"TOKEN_USAGE_CONTRACT_ANOMALY={e.get('token_usage_contract_anomaly')}",
        f"ATTEMPT_STATUS={payload.get('attempt_status')}",
        f"PROVIDER_CALL_SEQ={payload.get('provider_call_seq')}",
    ]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    args = ap.parse_args()
    db = Path(args.db).expanduser().resolve() if args.db else _default_db()
    if db is None or not db.is_file():
        print("RUNTIME_PROVIDER_EVIDENCE=NOT_AVAILABLE")
        return 2
    try:
        payload = _read_latest(db)
    except Exception as exc:
        print(f"RUNTIME_PROVIDER_EVIDENCE=NOT_AVAILABLE ({type(exc).__name__}: {exc})")
        return 3
    json_path, txt_path = _write_outputs(payload)
    print(f"RUNTIME_PROVIDER_EVIDENCE_JSON={json_path}")
    print(f"RUNTIME_PROVIDER_EVIDENCE_TXT={txt_path}")
    print(txt_path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
