from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.web.p0_app import create_p0_app


ROOT = Path(__file__).resolve().parents[1]


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            payload = b'{"status":"READY"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _write_runtime_config(path: Path) -> None:
    path.write_text(
        """active_model: hardware_fresh_extract
models:
  hardware_fresh_extract:
    provider: openai_compatible
    base_url: http://127.0.0.1:9/v1
    api_key_env: HARDWARE_CASE_API_KEY
    model: mock-model
    temperature: 0
    max_tokens: 8192
""",
        encoding="utf-8",
    )


def _startup_check(root: Path, work: Path) -> None:
    command = [
        sys.executable,
        str(root / "scripts/hardware_case_web_start.py"),
        "--check",
        "--db",
        str(work / "quality.db"),
        "--hardware-db",
        str(work / "hardware.db"),
        "--tree-upload-dir",
        str(work / "tree"),
        "--source-root",
        str(work / "sources"),
    ]
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or "RESULT=PASS" not in result.stdout:
        raise SystemExit(
            "FRESH_EXTRACT_STARTUP_CHECK_FAIL="
            + (result.stdout + result.stderr)[-1000:].replace("\n", " ")
        )


def main() -> int:
    manifest_path = ROOT / "PACKAGE_MANIFEST.json"
    if not manifest_path.is_file():
        raise SystemExit("PACKAGE_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_files = (
        "START_HARDWARE_CASE.bat",
        "START_HARDWARE_CASE.sh",
        "START_HARDWARE_CASE.command",
        "quality_knowledge/web/hardware_public_api.py",
        "quality_knowledge/web/hardware_operability_api.py",
        "services/hardware_operability.py",
        "schema/hardware_public_consumer_v1.schema.json",
    )
    for relative in required_files:
        if not (ROOT / relative).is_file():
            raise SystemExit("FRESH_EXTRACT_FILE_MISSING=" + relative)

    with tempfile.TemporaryDirectory(prefix="hardware-fresh-extract-") as temp:
        work = Path(temp)
        _startup_check(ROOT, work)

        model_config = work / "model.local.yaml"
        _write_runtime_config(model_config)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        secret = "FRESH_EXTRACT_SECRET_MUST_NOT_LEAK"
        old_env = dict(os.environ)
        try:
            os.environ["HARDWARE_CASE_MODEL_CONFIG"] = str(model_config)
            os.environ["HARDWARE_CASE_API_KEY"] = secret
            os.environ["HARDWARE_KNOWLEDGE_BASE_URL"] = f"http://127.0.0.1:{port}"
            os.environ["HARDWARE_KNOWLEDGE_RELEASE_VERSION"] = "CI_KNOWLEDGE_R1"
            os.environ["HARDWARE_KNOWLEDGE_READINESS_URL"] = (
                f"http://127.0.0.1:{port}/health"
            )

            app = create_p0_app(
                work / "quality-runtime.db",
                project_root=ROOT,
                hardware_case_db_path=work / "hardware-ready.db",
                hardware_tree_upload_dir=work / "tree-ready",
                hardware_case_source_root=work / "sources-ready",
                enabled_domains={"HARDWARE_CASE"},
            )
            client = TestClient(app)

            health = client.get("/health")
            if health.status_code != 200 or health.json().get("status") != "HEALTHY":
                raise SystemExit("FRESH_EXTRACT_HEALTH_FAIL")

            ready = client.get("/ready")
            if ready.status_code != 200 or ready.json().get("status") != "READY":
                raise SystemExit("FRESH_EXTRACT_READY_FAIL=" + ready.text[:1000])
            if secret in ready.text:
                raise SystemExit("SECRET_LEAK_IN_READINESS")

            descriptor = client.get("/api/public/hardware/v1/contract")
            if descriptor.status_code != 200:
                raise SystemExit("PUBLIC_CONTRACT_DESCRIPTOR_FAIL")
            binding = descriptor.json()
            if binding.get("public_contract_version") != "hardware-public-consumer/v1":
                raise SystemExit("PUBLIC_CONTRACT_VERSION_MISMATCH")
            if binding.get("source_commit") != manifest.get("source_commit"):
                raise SystemExit("SOURCE_COMMIT_BINDING_MISMATCH")
            if binding.get("schema_version") != manifest.get("schema_version"):
                raise SystemExit("SCHEMA_VERSION_BINDING_MISMATCH")
            if binding.get("product_version") != manifest.get("product_version"):
                raise SystemExit("PRODUCT_VERSION_BINDING_MISMATCH")
            if client.get("/api/public/hardware/v999/contract").status_code != 404:
                raise SystemExit("UNKNOWN_VERSION_NOT_FAIL_CLOSED")

            del os.environ["HARDWARE_CASE_MODEL_CONFIG"]
            missing = client.get("/ready")
            if missing.status_code != 503:
                raise SystemExit("MISSING_CONFIG_DID_NOT_FAIL_CLOSED")
            if missing.json()["dependencies"]["UNIFIED_RUNTIME_CONFIG"]["status"] != "UNREADY":
                raise SystemExit("MISSING_CONFIG_NOT_EXPLICIT")

            os.environ["HARDWARE_CASE_MODEL_CONFIG"] = str(model_config)
            os.environ["HARDWARE_KNOWLEDGE_READINESS_URL"] = "http://127.0.0.1:1/health"
            dependency = client.get("/ready")
            if dependency.status_code != 503:
                raise SystemExit("DEPENDENCY_UNREADY_DID_NOT_FAIL_CLOSED")
            if dependency.json()["dependencies"]["UNIFIED_KNOWLEDGE"]["status"] != "UNREADY":
                raise SystemExit("DEPENDENCY_UNREADY_NOT_EXPLICIT")
        finally:
            server.shutdown()
            server.server_close()
            os.environ.clear()
            os.environ.update(old_env)

    print("FRESH_EXTRACT_STARTUP=PASS")
    print("HEALTH=PASS")
    print("READINESS=PASS")
    print("DEPENDENCY_UNREADY=PASS")
    print("PUBLIC_CONTRACT_BINDING=PASS")
    print("UNKNOWN_VERSION_FAIL_CLOSED=PASS")
    print("CONFIG_VALIDATION=PASS")
    print("SECRET_SAFETY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
