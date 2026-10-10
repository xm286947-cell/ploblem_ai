#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import yaml
import os
import re
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

def safe_sqlite_snapshot(source: Path, destination: Path) -> None:
    """Make a transactionally consistent copy of a live SQLite/WAL database."""
    tmp = destination.with_name(destination.name + ".incoming")
    tmp.unlink(missing_ok=True)
    try:
        with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(tmp) as dst:
                src.backup(dst, pages=1000, sleep=0.05)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def require_real_provider_config(path: Path) -> Path:
    """Reject the controlled Mock and unconfigured real model without printing secrets."""
    if not path.is_file():
        raise ValueError("REAL_PROVIDER_MODEL_CONFIG_NOT_FOUND")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("REAL_PROVIDER_MODEL_CONFIG_INVALID")
    models = config.get("models") or {}
    active = str(config.get("active_model") or "").strip()
    model = models.get(active) if isinstance(models, dict) else None
    if not isinstance(model, dict):
        raise ValueError("REAL_PROVIDER_ACTIVE_MODEL_NOT_CONFIGURED")
    url = str(model.get("base_url") or "").lower()
    if "18090" in url or "mock" in str(model.get("model") or "").lower():
        raise ValueError("REFUSE_CONTROLLED_MOCK_FOR_REAL_PROVIDER")
    key_env = str(model.get("api_key_env") or "")
    url_env = str(model.get("base_url_env") or "")
    if key_env and not os.environ.get(key_env):
        raise ValueError("REAL_PROVIDER_CREDENTIAL_ENV_MISSING:" + key_env)
    if url_env and not os.environ.get(url_env):
        raise ValueError("REAL_PROVIDER_ENDPOINT_ENV_MISSING:" + url_env)
    if not (model.get("base_url") or (url_env and os.environ.get(url_env))):
        raise ValueError("REAL_PROVIDER_ENDPOINT_NOT_CONFIGURED")
    return path.resolve()


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


def _redact_log_line(value: str) -> str:
    """Redact likely credentials from console diagnostics."""
    value = re.sub(r'(?i)(bearer\s+)\S+', r'\1[REDACTED]', value)
    value = re.sub(
        r'(?i)((?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*)\S+',
        r'\1[REDACTED]',
        value,
    )
    return re.sub(r'(?i)\bsk-[A-Za-z0-9_-]{8,}', '[REDACTED]', value)

def report_app_failure(error: BaseException, process: subprocess.Popen | None, db_path: Path) -> None:
    print("WINDOWS_ACCEPTANCE_START=FAIL")
    print(f"STARTUP_ERROR_TYPE={type(error).__name__}")
    print(f"STARTUP_ERROR={_redact_log_line(str(error))}")
    print(f"APP_PROCESS_EXIT={process.poll() if process else 'NOT_STARTED'}")
    print(f"SOURCE_DB_EXISTS={db_path.is_file()}")
    print(f"SOURCE_DB_SIZE_BYTES={db_path.stat().st_size if db_path.is_file() else 0}")
    print(f"APP_LOG_PATH={APP_LOG}")
    print("APP_LOG_TAIL_BEGIN")
    try:
        lines = APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-160:]:
            print(_redact_log_line(line))
        if not lines:
            print("APP_LOG_EMPTY")
    except Exception as log_exc:
        print(f"APP_LOG_READ_FAIL={type(log_exc).__name__}")
    print("APP_LOG_TAIL_END")
    sys.stdout.flush()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--real-provider", action="store_true",
                        help="Isolated real business evaluation: no Mock Provider")
    parser.add_argument("--model-config", default="",
                        help="Existing authorized real model YAML; credentials stay in local environment")
    parser.add_argument(
        "--source-db",
        default="",
        help="Optional existing mature Quality DB. The runner copies it first and never writes the original file.",
    )
    args = parser.parse_args()

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    from tools.build_quality_scenario_test_fixture import build_fixture
    from tools.quality_scenario_functional_provider import configure

    if args.real_provider and not args.source_db:
        raise SystemExit("REAL_PROVIDER_REQUIRES_EXISTING_SOURCE_DB")
    model_config = None
    if args.real_provider:
        config_path = Path(args.model_config) if args.model_config else Path(
            os.environ.get("REVERSE_QUALITY_MODEL_CONFIG") or ROOT / "config/runtime/model.yaml"
        )
        try:
            model_config = require_real_provider_config(config_path)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            raise SystemExit(f"REAL_PROVIDER_PREFLIGHT_FAIL:{type(exc).__name__}:{exc}") from None

    source_mode = "CONTROLLED_FIXTURE"
    source_db = FIXTURE_SOURCE_DB
    qsv1_db = FIXTURE_QSV1_DB

    if args.source_db:
        original = Path(args.source_db).expanduser().resolve()
        if not original.is_file():
            raise SystemExit(f"ORIGINAL_DB_NOT_FOUND:{original}")
        for suffix in ("-wal", "-shm"):
            Path(str(ORIGINAL_DB_COPY) + suffix).unlink(missing_ok=True)
        if args.real_provider:
            safe_sqlite_snapshot(original, ORIGINAL_DB_COPY)
        else:
            # Preserve the existing controlled negative startup diagnostic gate.
            shutil.copy2(original, ORIGINAL_DB_COPY)
        source_mode = "ORIGINAL_DB_COPY_REAL_PROVIDER" if args.real_provider else "ORIGINAL_DB_COPY"
        source_db = ORIGINAL_DB_COPY
        # Real-provider runs keep an isolated durable QSV1 store; Mock runs
        # reset only their disposable acceptance store.
        if args.real_provider:
            db_fingerprint = hashlib.sha256(str(original).encode("utf-8")).hexdigest()[:12]
            qsv1_db = VALIDATION_DIR / f"quality_scenario_v1_real_{db_fingerprint}.db"
        else:
            qsv1_db = FIXTURE_QSV1_DB
            for path in (qsv1_db, Path(str(qsv1_db) + "-wal"), Path(str(qsv1_db) + "-shm")):
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
        if not args.real_provider:
            mock = subprocess.Popen(
                [sys.executable, str(ROOT / "tools" / "openai_mock" / "server.py"),
                 "--host", "127.0.0.1", "--port", "18090"],
                cwd=ROOT, stdout=mock_handle, stderr=subprocess.STDOUT,
            )
            configure("127.0.0.1", 18090)

        env = os.environ.copy()
        env["LEGACY_QUALITY_ISSUE_DB_PATH"] = str(source_db)
        env["QUALITY_SCENARIO_V1_DB_PATH"] = str(qsv1_db)
        env["REVERSE_QUALITY_MODEL_CONFIG"] = str(
            model_config if args.real_provider else ROOT / "config/runtime/model.w4-functional.yaml"
        )
        if args.real_provider:
            env.pop("W4_FUNCTIONAL_MOCK_API_KEY", None)
            env.pop("W4_FUNCTIONAL_FIXTURE", None)
        else:
            env["W4_FUNCTIONAL_MOCK_API_KEY"] = "test-only-controlled-provider"
            env["W4_FUNCTIONAL_FIXTURE"] = "1"

        app = subprocess.Popen(
            [
                sys.executable,
                "-u",
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
        startup_timeout = 300 if source_mode == "ORIGINAL_DB_COPY" else 45
        print(f"STARTUP_WAIT_LIMIT_SECONDS={startup_timeout}")
        print(f"STARTUP_SOURCE_MODE={source_mode}")
        print(f"STARTUP_SOURCE_DB={source_db}")
        print(f"STARTUP_LOG_PATH={APP_LOG}")
        # /issues is a full history page, not a readiness check. Large real DBs
        # can spend a long time querying issue history while the server is ready.
        readiness_url = base + "/openapi.json"
        print(f"STARTUP_HEALTH_URL={readiness_url}")
        deadline = time.monotonic() + startup_timeout
        next_progress = time.monotonic() + 10
        startup_error = None
        ready = False
        while time.monotonic() < deadline:
            if app.poll() is not None:
                startup_error = RuntimeError(f"APP_EXITED_EARLY:{app.returncode}")
                break
            try:
                with urlopen(readiness_url, timeout=1.0) as response:
                    if response.status == 200:
                        ready = True
                        break
            except (URLError, TimeoutError, OSError) as exc:
                startup_error = exc
            now = time.monotonic()
            if now >= next_progress:
                elapsed = int(startup_timeout - max(0, deadline - now))
                log_bytes = APP_LOG.stat().st_size if APP_LOG.exists() else 0
                print(f"STARTUP_WAITING elapsed={elapsed}s app_alive={app.poll() is None} app_log_bytes={log_bytes}")
                next_progress = now + 10
            time.sleep(1.0)

        if not ready:
            error = startup_error if app.poll() is not None else RuntimeError(
                f"HTTP_NOT_READY_AFTER_{startup_timeout}s:{startup_error}"
            )
            report_app_failure(error, app, source_db)
            raise error

        try:
            # /issues performs history-wide work and must not gate startup
            # in real-DB mode. The QSV1 workbench routes are the requested entry.
            for route in (
                "/software-assessment",
                "/quality-scenarios/workbench",
                "/quality-scenarios/library",
            ):
                print(f"PRODUCT_ROUTE_CHECK={route}")
                wait_http(base + route, timeout=10)
            if source_mode == "CONTROLLED_FIXTURE":
                print("PRODUCT_ROUTE_CHECK=/issues")
                wait_http(base + "/issues", timeout=10)
            else:
                print("ISSUES_FULL_HISTORY_PAGE_CHECK=NOT_A_STARTUP_GATE")
        except Exception as error:
            report_app_failure(error, app, source_db)
            raise

        print("TASK=QUALITY_SCENARIO_WINDOWS_ACCEPTANCE_001")
        print("PLATFORM=Windows")
        print(f"MODE={source_mode}")
        print("MATURE_RUNTIME_ROOT=quality_knowledge.web.app.create_app")
        print("PRODUCT_ENTRY=/software-assessment#quality-scenario-production")
        print("DIRECT_QSV1_CANDIDATE_WRITE=NO")
        print("DIRECT_QSV1_PUBLISH_WRITE=NO")
        print("PROVIDER=" + ("REAL_CONFIGURED" if args.real_provider else "CONTROLLED_OPENAI_COMPATIBLE_MOCK"))
        print("REAL_PROVIDER_SEMANTIC_GATE=" + ("PENDING_USER_CASE_ACCEPTANCE" if args.real_provider else "OUT_OF_SCOPE"))
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
                "USER_ORIGINAL_DB_AS_IS"
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
