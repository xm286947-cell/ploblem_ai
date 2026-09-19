"""Synthetic REQ-022 database benchmark (AI time deliberately excluded)."""
from __future__ import annotations

from pathlib import Path
import json
import os
import platform
import statistics
import time

from .repository import MajorKnowledgeRepository


def _percentile(values: list[float], percent: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    return values[min(len(values) - 1, int((len(values) - 1) * percent))]


def run_synthetic_benchmark(
    repository: MajorKnowledgeRepository,
    *,
    event_count: int = 10_000,
    fragment_count: int = 100_000,
    iterations: int = 40,
) -> dict:
    started = time.perf_counter()
    case_count = max(1, event_count // 5)
    with repository.transaction() as connection:
        for index in range(case_count):
            case_id = f"BENCH-CASE-{index:06d}"
            connection.execute(
                "INSERT OR IGNORE INTO kb_case(case_id,title,group_code,status) VALUES(?,?,?,'ACTIVE')",
                (case_id, f"合成案例{index}", f"G{index % 5}"),
            )
        connection.executemany(
            "INSERT OR IGNORE INTO kb_event(event_id,case_id,standard_itr,internal_event_key,event_title,group_code) VALUES(?,?,?,?,?,?)",
            [
                (f"BENCH-EVT-{index:06d}", f"BENCH-CASE-{index // 5:06d}", f"ITR2026{index:06d}", f"ITR2026{index:06d}", f"事件{index}", f"G{(index // 5) % 5}")
                for index in range(event_count)
            ],
        )
        for doc_index in range(max(1, fragment_count // 100)):
            document_id = f"BENCH-DOC-{doc_index:06d}"
            version_id = f"BENCH-VER-{doc_index:06d}"
            group = f"G{doc_index % 5}"
            connection.execute("INSERT OR IGNORE INTO kb_document(document_id,group_code,logical_name) VALUES(?,?,?)", (document_id, group, document_id))
            connection.execute(
                """INSERT OR IGNORE INTO kb_document_version(version_id,document_id,version_no,content_hash,original_filename,media_type,attachment_path,size_bytes,parse_status)
                   VALUES(?,?,1,?,?, 'PDF',?,1,'SUCCESS')""",
                (version_id, document_id, f"{doc_index:064x}"[-64:], f"{document_id}.pdf", f"{group}/{document_id}.pdf"),
            )
        batch = []
        for index in range(fragment_count):
            doc_index = index // 100
            batch.append((f"BENCH-FRAG-{index:07d}", f"BENCH-VER-{doc_index:06d}", index % 100 + 1, "根因", "PAGE", f"page:{index % 20 + 1}", "TEXT", f"合成片段 {index} 根因与措施", f"{index:064x}"[-64:]))
            if len(batch) == 5000:
                connection.executemany("INSERT OR IGNORE INTO kb_fragment(fragment_id,version_id,ordinal,section_path,location_type,location_ref,fragment_type,text_content,text_hash) VALUES(?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            connection.executemany("INSERT OR IGNORE INTO kb_fragment(fragment_id,version_id,ordinal,section_path,location_type,location_ref,fragment_type,text_content,text_hash) VALUES(?,?,?,?,?,?,?,?,?)", batch)
    load_seconds = time.perf_counter() - started

    timings = {"list": [], "batch_association": [], "detail": []}
    with repository.connect() as connection:
        # warm cache
        connection.execute("SELECT COUNT(*) FROM kb_fragment").fetchone()
        for index in range(iterations):
            mark = time.perf_counter()
            connection.execute("SELECT case_id,title,status FROM kb_case WHERE group_code=? AND archived_at IS NULL ORDER BY updated_at DESC LIMIT 20 OFFSET ?", (f"G{index % 5}", (index % 20) * 20)).fetchall()
            timings["list"].append((time.perf_counter() - mark) * 1000)
            values = [f"ITR2026{(index * 50 + offset) % event_count:06d}" for offset in range(50)]
            mark = time.perf_counter()
            placeholders = ",".join("?" for _ in values)
            connection.execute(f"SELECT standard_itr,event_id FROM kb_event WHERE group_code=? AND standard_itr IN ({placeholders})", [f"G{(index * 10) // 5 % 5}", *values]).fetchall()
            timings["batch_association"].append((time.perf_counter() - mark) * 1000)
            mark = time.perf_counter()
            connection.execute("SELECT * FROM kb_fragment WHERE version_id=? ORDER BY ordinal LIMIT 100", (f"BENCH-VER-{index % max(1, fragment_count // 100):06d}",)).fetchall()
            timings["detail"].append((time.perf_counter() - mark) * 1000)
        plans = {
            "list": [tuple(row) for row in connection.execute("EXPLAIN QUERY PLAN SELECT case_id,title,status FROM kb_case WHERE group_code='G1' AND archived_at IS NULL ORDER BY updated_at DESC LIMIT 20")],
            "association": [tuple(row) for row in connection.execute("EXPLAIN QUERY PLAN SELECT event_id FROM kb_event WHERE group_code='G1' AND standard_itr='ITR2026000010'")],
            "detail": [tuple(row) for row in connection.execute("EXPLAIN QUERY PLAN SELECT * FROM kb_fragment WHERE version_id='BENCH-VER-000001' ORDER BY ordinal LIMIT 100")],
        }
    metrics = {
        name: {"p50_ms": round(statistics.median(values), 3), "p95_ms": round(_percentile(values, 0.95), 3), "max_ms": round(max(values), 3)}
        for name, values in timings.items()
    }
    return {
        "event_count": event_count,
        "fragment_count": fragment_count,
        "load_seconds": round(load_seconds, 3),
        "iterations": iterations,
        "warm_cache_database_only": True,
        "ai_time_included": False,
        "target_p95_ms": 1000,
        "target_met": all(value["p95_ms"] <= 1000 for value in metrics.values()),
        "metrics": metrics,
        "query_plans": plans,
        "hardware": {
            "platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
            "cpu_count": os.cpu_count(), "python": platform.python_version(),
        },
    }
