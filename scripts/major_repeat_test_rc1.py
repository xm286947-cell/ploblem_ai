"""Start and smoke-test the existing P0 Web for the RC1 test package."""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from builder.m6_runner import run_m6
from quality_knowledge.p0.initializer import P0Initializer
from quality_knowledge.p0.repository import P0Repository
from quality_knowledge.web.p0_app import create_p0_app
from repositories import JsonArtifactRepository
from test_repeat_web_mvp import _save_issue, _seed_case_artifacts


def initialize(project_root: Path, db_path: Path, *, demo: bool) -> None:
    initializer = P0Initializer(
        manifest_path=project_root / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=project_root / "quality_knowledge/config/plc_fields.yaml",
    )
    if db_path.exists():
        initializer.verify_ready(db_path)
    else:
        initializer.initialize(db_path)
    if not demo:
        return
    repository = P0Repository(db_path)
    if repository.get_issue("K-ITR-1"):
        return
    _save_issue(
        repository, knowledge_id="K-ITR-1", business_id="ITR-1",
        title="当前控制器掉电后启动失败", raw_extra={"关联漏测问题": "MISS-1"},
    )
    _save_issue(
        repository, knowledge_id="K-MISS-1", business_id="MISS-1",
        title="掉电恢复场景漏测", raw_extra={"测试缺口": "未覆盖写入中的掉电"},
    )
    _save_issue(
        repository, knowledge_id="K-ITR-2", business_id="ITR-2",
        title="当前控制器偶发重启",
    )
    _seed_case_artifacts(JsonArtifactRepository(project_root))
    m6 = run_m6(project_root, overwrite=True)
    if m6["failed_count"] or m6["success_count"] != 1:
        raise RuntimeError(f"SYNTHETIC_RETRIEVAL_INDEX_FAILED: {m6['failures']}")


def smoke() -> int:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory(prefix="major-repeat-test-rc1-") as directory:
        temporary = Path(directory)
        shutil.copytree(ROOT / "config", temporary / "config")
        shutil.copytree(ROOT / "schema", temporary / "schema")
        shutil.copytree(ROOT / "quality_knowledge/config", temporary / "quality_knowledge/config")
        db_path = temporary / "p0.sqlite3"
        initialize(temporary, db_path, demo=True)
        client = TestClient(create_p0_app(db_path, stage_runner=object(), project_root=temporary))
        assert client.get("/api/v2/initialization/status").json()["initialization_state"] == "READY"
        assert client.get("/p0/issues/K-ITR-1").status_code == 200
        assert client.get("/p0/cases").status_code == 200
        query = client.post(
            "/api/v2/issues/K-ITR-1/repeat-risk/queries",
            json={"include_missed_test": True, "top_k": 5},
        )
        assert query.status_code == 201, query.text
        result = query.json()["result"]
        assert result["result_status"] == "READY_FOR_REVIEW"
        assert result["candidates"][0]["why_relevant"]
        assert result["candidates"][0]["evidence"]
        saved = client.post(
            f"/api/v2/repeat-risk/queries/{result['query_id']}/decision",
            json={"decision": "SIMILAR", "decided_by": "package-smoke", "reason": "synthetic evidence"},
        )
        assert saved.status_code == 200
        assert client.get("/api/v2/issues/K-ITR-1/repeat-risk/result").json()["result"]["human_decision"]["decision"] == "SIMILAR"
        cases = client.get("/api/v2/historical-cases").json()
        assert cases["total"] == 1 and cases["items"][0]["status"] == "PUBLISHED"
        assert client.get("/api/v2/historical-cases/HCASE-1").json()["evidence"]
    print("WEB_STARTUP=PASS")
    print("SYNTHETIC_REPEAT_QUERY=PASS")
    print("SYNTHETIC_CASE_LIBRARY=PASS")
    return 0


def serve(db_path: Path, *, demo: bool, host: str, port: int, browser: bool) -> int:
    import uvicorn

    initialize(ROOT, db_path, demo=demo)
    app = create_p0_app(db_path, project_root=ROOT)
    url = f"http://{host}:{port}/p0/issues/K-ITR-1" if demo else f"http://{host}:{port}/p0/issues"
    print(f"TEST_DATA={'SYNTHETIC' if demo else 'TARGET_ENV'}")
    print(f"WEB_URL={url}")
    print("TARGET_ENV=PENDING")
    print("MVP_READY=NO")
    if browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "smoke"))
    parser.add_argument("--target", action="store_true", help="Use target data; never seed synthetic demo data")
    parser.add_argument("--db", type=Path, default=ROOT.parent / "data/runtime/quality_capability_p0.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.action == "smoke":
        return smoke()
    return serve(args.db, demo=not args.target, host=args.host, port=args.port, browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
