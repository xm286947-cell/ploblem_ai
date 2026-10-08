#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VALIDATION_DIR = ROOT / "validation" / "windows_acceptance"
FIXTURE_SOURCE_DB = VALIDATION_DIR / "quality_scenario_source_fixture.db"
FIXTURE_QSV1_DB = VALIDATION_DIR / "quality_scenario_v1_windows.db"
ORIGINAL_DB_COPY = VALIDATION_DIR / "original_quality_db_copy.db"
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
    parser.add_argument(
        "--source-db",
        default="",
        help="Optional existing mature Quality DB. The runner copies it first and never writes the original file.",
    )
    args = parser.parse_args()

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    from tools.build_quality_scenario_test_fixture import augment_fixture, build_fixture
    from tools.quality_scenario_functional_provider import configure

    source_mode = "CONTROLLED_FIXTURE"
    source_db = FIXTURE_SOURCE_DB
    qsv1_db = FIXTURE_QSV1_DB

    if args.source_db:
        original = Path(args.source_db).expanduser().resolve()
        if not original.is_file():
            raise SystemExit(f"ORIGINAL_DB_NOT_FOUND:{original}")
        for suffix in ("", "-wal", "-shm"):
            Path(str(ORIGINAL_DB_COPY) + suffix).unlink(missing_ok=True)
        shutil.copy2(original, ORIGINAL_DB_COPY)
        # Add the controlled G1-G5 source cases to the COPY only. This keeps
        # original mature data/history visible while making the Windows
        # acceptance checklist deterministic and keeps G5 revision available.
        augment_fixture(ORIGINAL_DB_COPY)
        source_mode = "ORIGINAL_DB_COPY_PLUS_CONTROLLED_CASES"
        source_db = ORIGINAL_DB_COPY
        # Keep QSV1 lifecycle writes in a dedicated Windows acceptance DB.
        # The supplied mature DB is source/business data only and remains
        # untouched; its copy is also not used as the QSV1 write store.
        qsv1_db = FIXTURE_QSV1_DB
        for path in (
            qsv1_db,
            Path(str(qsv1_db) + "-wal"),
            Path(str(qsv1_db) + "-shm"),
        ):
            path.unlink(missing_ok=True)
    else:
        manifest_path = FIXTURE_SOURCE_DB.with_suffix(FIXTURE_SOURCE_DB.suffix + ".fixture.json")
        if FIXTURE_SOURCE_DB.exists():
            if manifest_path.exists():
                FIXTURE_SOURCE_DB.unlink()
                manifest_path.unlink(missing_ok=True)
            else:
                raise SystemExit("REFUSE_RESET_NON_FIXTURE_DB")
        build_fixture(FIXTURE_SOURCE_DB)
        for path in (
            FIXTURE_QSV1_DB,
            Path(str(FIXTURE_QSV1_DB) + "-wal"),
            Path(str(FIXTURE_QSV1_DB) + "-shm"),
        ):
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
        env["LEGACY_QUALITY_ISSUE_DB_PATH"] = str(source_db)
        env["QUALITY_SCENARIO_V1_DB_PATH"] = str(qsv1_db)
        env["REVERSE_QUALITY_MODEL_CONFIG"] = str(ROOT / "config" / "runtime" / "model.w4-functional.yaml")
        env["W4_FUNCTIONAL_MOCK_API_KEY"] = "test-only-controlled-provider"
        env["W4_FUNCTIONAL_FIXTURE"] = "1"

        app = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "main.py"),
                "knowledge-web",
                "--db",
                str(source_db),
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
        try:
            wait_http(base + "/issues", timeout=45)
        except Exception:
            print("WINDOWS_ACCEPTANCE_START=FAIL")
            print(f"APP_LOG={APP_LOG}")
            try:
                if APP_LOG.is_file():
                    lines = APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                    print("----- APP LOG TAIL -----")
                    for line in lines[-120:]:
                        print(line)
                    print("----- END APP LOG TAIL -----")
            except Exception as log_exc:
                print(f"APP_LOG_READ_FAIL={type(log_exc).__name__}")
            raise
        for route in (
            "/issues",
            "/software-assessment",
            "/quality-scenarios/workbench",
            "/quality-scenarios/library",
        ):
            wait_http(base + route, timeout=10)

        print("TASK=QUALITY_SCENARIO_WINDOWS_ACCEPTANCE_001")
        print("PLATFORM=Windows")
        print(f"MODE={source_mode}")
        print("MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app")
        print("PRODUCT_ENTRY=/software-assessment#quality-scenario-production")
        print("DIRECT_QSV1_CANDIDATE_WRITE=NO")
        print("DIRECT_QSV1_PUBLISH_WRITE=NO")
        print("PROVIDER=CONTROLLED_OPENAI_COMPATIBLE_MOCK")
        print("REAL_PROVIDER_SEMANTIC_GATE=OUT_OF_SCOPE")
        print(f"SOURCE_DB={source_db}")
        print(f"QSV1_DB={qsv1_db}")
        print(
            f"ORIGINAL_DB_MUTATED={'NO' if source_mode.startswith('ORIGINAL_DB_COPY') else 'N/A'}"
        )
        print(
            "SOURCE_AND_QSV1_DB_SEPARATED="
            + (
                "YES_SOURCE_COPY_PLUS_SEPARATE_QSV1"
                if source_mode.startswith("ORIGINAL_DB_COPY")
                else "YES"
            )
        )
        print(
            "G1_G5_SOURCE_DATA="
            + (
                "USER_ORIGINAL_DB_PLUS_CONTROLLED_CASES"
                if source_mode.startswith("ORIGINAL_DB_COPY")
                else "READY"
            )
        )
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
