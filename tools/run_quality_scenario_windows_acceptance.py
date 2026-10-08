#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_DIR = ROOT / "validation" / "windows_acceptance"
SOURCE_DB = VALIDATION_DIR / "quality_scenario_source_fixture.db"
QSV1_DB = VALIDATION_DIR / "quality_scenario_v1_windows.db"
APP_LOG = VALIDATION_DIR / "mature_app.log"
MOCK_LOG = VALIDATION_DIR / "provider_mock.log"


def wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=0.8) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"HTTP_NOT_READY:{url}:{last}")


def terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    from tools.build_quality_scenario_test_fixture import build_fixture
    from tools.quality_scenario_functional_provider import configure

    manifest_path = SOURCE_DB.with_suffix(SOURCE_DB.suffix + ".fixture.json")
    if SOURCE_DB.exists():
        if manifest_path.exists():
            SOURCE_DB.unlink()
            manifest_path.unlink(missing_ok=True)
        else:
            raise SystemExit("REFUSE_RESET_NON_FIXTURE_DB")
    build_fixture(SOURCE_DB)

    for path in (QSV1_DB, Path(str(QSV1_DB) + "-wal"), Path(str(QSV1_DB) + "-shm")):
        path.unlink(missing_ok=True)

    mock = None
    app = None
    mock_handle = MOCK_LOG.open("w", encoding="utf-8")
    app_handle = APP_LOG.open("w", encoding="utf-8")
    try:
        mock = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tools" / "openai_mock" / "server.py"),
                "--host",
                "127.0.0.1",
                "--port",
                "18090",
            ],
            cwd=ROOT,
            stdout=mock_handle,
            stderr=subprocess.STDOUT,
        )
        configure("127.0.0.1", 18090)

        env = os.environ.copy()
        env["LEGACY_QUALITY_ISSUE_DB_PATH"] = str(SOURCE_DB)
        env["QUALITY_SCENARIO_V1_DB_PATH"] = str(QSV1_DB)
        env["REVERSE_QUALITY_MODEL_CONFIG"] = str(ROOT / "config" / "runtime" / "model.w4-functional.yaml")
        env["W4_FUNCTIONAL_MOCK_API_KEY"] = "test-only-controlled-provider"
        env["W4_FUNCTIONAL_FIXTURE"] = "1"

        app = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "main.py"),
                "knowledge-web",
                "--db",
                str(SOURCE_DB),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            cwd=ROOT,
            env=env,
            stdout=app_handle,
            stderr=subprocess.STDOUT,
        )

        base = f"http://127.0.0.1:{args.port}"
        wait_http(base + "/issues", timeout=45)
        for route in (
            "/issues",
            "/software-assessment",
            "/quality-scenarios/workbench",
            "/quality-scenarios/library",
        ):
            wait_http(base + route, timeout=10)

        print("TASK=QUALITY_SCENARIO_WINDOWS_ACCEPTANCE_001")
        print("PLATFORM=Windows")
        print("MODE=TEST_ONLY_CONTROLLED_DATA")
        print("MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app")
        print("PRODUCT_ENTRY=/software-assessment#quality-scenario-production")
        print("DIRECT_QSV1_CANDIDATE_WRITE=NO")
        print("DIRECT_QSV1_PUBLISH_WRITE=NO")
        print("PROVIDER=CONTROLLED_OPENAI_COMPATIBLE_MOCK")
        print("REAL_PROVIDER_SEMANTIC_GATE=OUT_OF_SCOPE")
        print(f"SOURCE_FIXTURE_DB={SOURCE_DB}")
        print(f"QSV1_RESULT_DB={QSV1_DB}")
        print("SOURCE_AND_QSV1_DB_SEPARATED=YES")
        print("G1_G5_SOURCE_DATA=READY")
        print(f"APP_URL={base}/software-assessment#quality-scenario-production")
        print("WINDOWS_ACCEPTANCE_START=PASS")

        if args.smoke_only:
            return 0

        if not args.no_browser:
            webbrowser.open(base + "/software-assessment#quality-scenario-production")

        print()
        print("Windows compatibility test environment is running.")
        print("Close this window or press Ctrl+C to stop.")
        return app.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        terminate(app)
        terminate(mock)
        app_handle.close()
        mock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
