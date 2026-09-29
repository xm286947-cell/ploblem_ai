from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_overall_r2_windows_candidate.py"
SECRET = "R2_E2E_CHILD_SECRET_DO_NOT_RENDER"


def _wait_json(url: str, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last = exc
        time.sleep(1)
    raise AssertionError(f"URL did not become ready: {url}: {last}")


def _json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def _multipart(
    url: str,
    *,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
) -> dict:
    boundary = "----r2e2e" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        )
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n'
        ).encode()
    )
    body.extend(b"Content-Type: application/pdf\r\n\r\n")
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.mark.skipif(os.name != "nt", reason="Windows candidate E2E")
def test_false_pass_closure_runs_real_built_zip_pdf_and_secretref_child_process(tmp_path: Path):
    dist = tmp_path / "dist"
    build = subprocess.run(
        [sys.executable, str(BUILDER), "--output-dir", str(dist)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    values = dict(
        line.split("=", 1)
        for line in build.stdout.splitlines()
        if "=" in line
    )
    package_zip = Path(values["PACKAGE_ZIP"])
    assert package_zip.is_file()

    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(package_zip) as bundle:
        bundle.extractall(extracted)
    package_root = next(path for path in extracted.iterdir() if path.is_dir())

    root_bats = sorted(path.name for path in package_root.glob("*.bat"))
    assert root_bats == [
        "INSTALL_OVERALL_R2_WINDOWS.bat",
        "START_OVERALL_R2_WINDOWS.bat",
        "STOP_OVERALL_R2_WINDOWS.bat",
    ]
    readme = (package_root / "00_README_FIRST.txt").read_text(encoding="utf-8")
    assert "OVERALL R2" in readme
    assert "ONLY USER STARTUP PATH" in readme
    assert "HARDWARE CASE PRODUCT TEST FULL V0.1" not in readme

    env = os.environ.copy()
    env["OVERALL_R2_NO_PAUSE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    install = subprocess.run(
        ["cmd.exe", "/c", str(package_root / "INSTALL_OVERALL_R2_WINDOWS.bat")],
        cwd=package_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    package_python = package_root / ".venv" / "Scripts" / "python.exe"
    assert package_python.is_file()

    legacy_db = tmp_path / "legacy.db"
    seed_legacy = subprocess.run(
        [
            str(package_python),
            "-c",
            (
                "from quality_knowledge.web import create_app;"
                f"create_app(r'{legacy_db}')"
            ),
        ],
        cwd=package_root,
        env={**env, "PYTHONPATH": str(package_root)},
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert seed_legacy.returncode == 0, seed_legacy.stdout + seed_legacy.stderr
    assert legacy_db.is_file()

    data_root = tmp_path / "data"
    control_root = data_root / "overall_runtime_control"
    revision_code = textwrap.dedent(
        f"""
        from pathlib import Path
        from quality_knowledge.web.overall_runtime_control import (
            ConfigRevisionRequest,
            OverallRuntimeControlPlane,
        )
        root = Path(r"{package_root}")
        state = Path(r"{control_root}")
        control = OverallRuntimeControlPlane(
            project_root=root,
            state_root=state,
            runtime_dbs={{}},
        )
        revision = control.create_revision(
            ConfigRevisionRequest(
                active_model="qwen_prod",
                models={{
                    "qwen_prod": {{
                        "provider": "openai_compatible",
                        "base_url_env": "R2_E2E_PROVIDER_BASE_URL",
                        "api_key_env": "acca1",
                        "model": "mock-gpt",
                        "temperature": 0,
                        "max_tokens": 8192,
                    }}
                }},
                note="built-zip child-process SecretRef acceptance",
            ),
            actor="windows-package-e2e",
        )
        control.activate_revision(
            revision["revision_id"],
            actor="windows-package-e2e",
        )
        print(revision["revision_id"])
        """
    )
    revision = subprocess.run(
        [str(package_python), "-c", revision_code],
        cwd=package_root,
        env={**env, "PYTHONPATH": str(package_root)},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert revision.returncode == 0, revision.stdout + revision.stderr
    revision_id = revision.stdout.strip().splitlines()[-1]
    assert revision_id

    mock_log = tmp_path / "mock.log"
    with mock_log.open("w", encoding="utf-8") as mock_stream:
        mock = subprocess.Popen(
            [
                str(package_python),
                str(package_root / "tests" / "support" / "r2_false_pass_openai_mock.py"),
                "--port",
                "18081",
            ],
            cwd=package_root,
            env={**env, "PYTHONPATH": str(package_root)},
            stdout=mock_stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        _wait_json("http://127.0.0.1:18081/__e2e__/health", timeout=30)

        child_env = env.copy()
        child_env["OVERALL_R2_DATA_DIR"] = str(data_root)
        child_env["LEGACY_QUALITY_ISSUE_DB_PATH"] = str(legacy_db)
        child_env["R2_E2E_PROVIDER_BASE_URL"] = "http://127.0.0.1:18081/v1"
        child_env["acca1"] = SECRET
        child_env.pop("DASHSCOPE_BASE_URL", None)
        child_env.pop("DASHSCOPE_API_KEY", None)

        start_log = tmp_path / "start.log"
        with start_log.open("w", encoding="utf-8") as stream:
            start = subprocess.Popen(
                [
                    "cmd.exe",
                    "/c",
                    str(package_root / "START_OVERALL_R2_WINDOWS.bat"),
                    "--no-browser",
                    "--port",
                    "18088",
                ],
                cwd=package_root,
                env=child_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
        try:
            _wait_json(
                "http://127.0.0.1:18088/storage-workspace/api/health",
                timeout=180,
            )

            start_text = start_log.read_text(
                encoding="utf-8",
                errors="replace",
            )
            assert f"ACTIVE_REVISION={revision_id}" in start_text
            assert "MODEL_REF=qwen_prod" in start_text
            assert "BASE_URL_ENV_REF=R2_E2E_PROVIDER_BASE_URL" in start_text
            assert "BASE_URL_PRESENT=PRESENT" in start_text
            assert "API_KEY_ENV_REF=acca1" in start_text
            assert "API_KEY_PRESENT=PRESENT" in start_text
            assert "KNOWLEDGE_RELEASE_INIT_STATUS=SEEDED_FROM_PACKAGE" in start_text
            assert "KNOWLEDGE_RELEASE_VERSION=KP-STORAGE-RC1-VALIDATION-001" in start_text
            assert SECRET not in start_text

            knowledge = _json(
                "http://127.0.0.1:18088/storage-workspace/api/product/knowledge/status"
            )
            assert knowledge["available"] is True
            assert knowledge["knowledge_release_version"] == "KP-STORAGE-RC1-VALIDATION-001"

            pdf_path = package_root / "products" / "storage_rc1" / "examples" / "synthetic_emmc.pdf"
            pdf_bytes = pdf_path.read_bytes()
            identify = _multipart(
                "http://127.0.0.1:18088/storage-workspace/api/documents/identify",
                fields={},
                file_field="file",
                filename=pdf_path.name,
                file_bytes=pdf_bytes,
            )
            vendor = str((identify.get("vendor") or {}).get("value") or "").strip()
            model = str((identify.get("model") or {}).get("value") or "").strip()
            device_type = str((identify.get("device_type") or {}).get("value") or "").strip()
            assert vendor
            assert model
            assert device_type == "eMMC"

            document_identity = identify.get("document_identity") or {}
            models = identify.get("models") or []
            job = _multipart(
                "http://127.0.0.1:18088/storage-workspace/api/documents/jobs",
                fields={
                    "vendor": vendor,
                    "model": model,
                    "device_type": device_type,
                    "models_json": json.dumps(models, ensure_ascii=False),
                    "document_number": str(document_identity.get("document_number") or ""),
                    "revision": str(document_identity.get("revision") or ""),
                    "revision_date": str(document_identity.get("revision_date") or ""),
                    "document_variant": str(document_identity.get("document_variant") or ""),
                },
                file_field="file",
                filename=pdf_path.name,
                file_bytes=pdf_bytes,
            )
            job_id = job["job_id"]
            deadline = time.time() + 240
            state = {}
            while time.time() < deadline:
                state = _json(
                    f"http://127.0.0.1:18088/storage-workspace/api/documents/jobs/{job_id}"
                )
                if state.get("status") in {"completed", "failed"}:
                    break
                time.sleep(1)
            assert state.get("status") == "completed", state

            result = state.get("result") or {}
            device_id = str(result.get("device_id") or "")
            assert device_id
            assert isinstance(result.get("coverage"), dict)

            runtime_facts = _json(
                f"http://127.0.0.1:18088/storage-workspace/api/devices/{device_id}/runtime-facts"
            )
            assert isinstance(runtime_facts.get("coverage"), dict)
            assert "review_required" in runtime_facts

            review = _json(
                f"http://127.0.0.1:18088/storage-workspace/api/product/devices/{device_id}/review-workbench"
            )
            assert isinstance(review, dict)

            status = _json(
                "http://127.0.0.1:18088/storage-workspace/api/v1/runtime/status"
            )
            assert status["configured"] is True
            assert status["profile"] == "qwen_prod"
            assert status["api_key_env"] == "acca1"
            assert status["api_key_present"] is True

            stats = _json("http://127.0.0.1:18081/__e2e__/stats")
            assert stats["requests"] >= 2, stats
            assert stats["auth_present"] == stats["requests"], stats

            web_log = tmp_path / "logs" / "overall-r2.log"
            if web_log.is_file():
                assert SECRET not in web_log.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
        finally:
            stop = subprocess.run(
                ["cmd.exe", "/c", str(package_root / "STOP_OVERALL_R2_WINDOWS.bat")],
                cwd=package_root,
                env=child_env,
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
            try:
                start.wait(timeout=60)
            except subprocess.TimeoutExpired:
                start.kill()
            assert stop.returncode == 0, stop.stdout + stop.stderr
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mock.kill()
