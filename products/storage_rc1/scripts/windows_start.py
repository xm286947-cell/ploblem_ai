from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(os.environ.get("UNIFIED_AGENT_RUNTIME_ROOT") or (ROOT / "vendor" / "unified_agent_runtime")).resolve()
ROOT_LOG = ROOT / "RUN_LOG.txt"
APP_LOG = ROOT / "release" / "storage_app.log"


class DualWriter:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            try:
                stream.write(text)
                stream.flush()
            except Exception:
                pass
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass

    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        # Uvicorn/Colorama probe this during logging setup. A tee stream must
        # behave like the primary console stream instead of hiding the API.
        try:
            return bool(self.streams[0].isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        # Delegate file-descriptor queries to the primary stream when present.
        return self.streams[0].fileno()

    def writable(self) -> bool:
        try:
            return bool(self.streams[0].writable())
        except Exception:
            return True


def _configure_logging() -> tuple[object, object]:
    ROOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    APP_LOG.parent.mkdir(parents=True, exist_ok=True)
    root_file = ROOT_LOG.open("a", encoding="utf-8", errors="replace", buffering=1)
    app_file = APP_LOG.open("w", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = DualWriter(sys.__stdout__, root_file, app_file)  # type: ignore[assignment]
    sys.stderr = DualWriter(sys.__stderr__, root_file, app_file)  # type: ignore[assignment]
    return root_file, app_file


def _run_check(args: list[str]) -> None:
    print("[startup-check]", " ".join(args))
    completed = subprocess.run(
        args,
        cwd=str(ROOT),
        env=os.environ.copy(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _resolve_model_config() -> Path:
    raw = os.environ.get("STORAGE_MODEL_CONFIG", "").strip()
    path = Path(raw).expanduser() if raw else ROOT / "config" / "model.local.yaml"
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"STARTUP_CONFIG_MISSING: {path}")
    os.environ["STORAGE_MODEL_CONFIG"] = str(path)
    return path


def _configure_no_proxy(model_config: Path) -> None:
    try:
        raw = yaml.safe_load(model_config.read_text(encoding="utf-8")) or {}
        models = raw.get("models") or {}
        active = str(raw.get("active_model") or "qwen_prod")
        profile = models.get(active) or models.get("qwen_prod") or {}
        base_url = str(profile.get("base_url") or "").strip()
        if not base_url:
            env_name = str(profile.get("base_url_env") or "").strip()
            base_url = os.environ.get(env_name, "").strip() if env_name else ""
        host = urlsplit(base_url).hostname
        if not host:
            return
        for name in ("NO_PROXY", "no_proxy"):
            items = [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]
            for item in (host, "127.0.0.1", "localhost"):
                if item not in items:
                    items.append(item)
            os.environ[name] = ",".join(items)
        print(f"[startup] NO_PROXY includes provider host: {host}")
    except Exception as exc:
        print(f"[startup] NO_PROXY setup warning: {type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    handles = _configure_logging()
    try:
        print("============================================================")
        print("STORAGE WINDOWS NORMAL STARTUP")
        print("MODE=SERVER_ONLY")
        print("PRODUCT_E2E=NOT_RUN")
        print("MOCK=NOT_RUN")
        print("FAULT_TESTS=NOT_RUN")
        print("NETWORK_OWNER=UNIFIED_AGENT_RUNTIME_ONLY")
        print("============================================================")

        model_config = _resolve_model_config()
        _configure_no_proxy(model_config)

        _run_check([
            sys.executable,
            str(ROOT / "scripts" / "preflight.py"),
            "--runtime-root",
            str(RUNTIME_ROOT),
            "--mode",
            "real",
        ])
        _run_check([sys.executable, str(ROOT / "scripts" / "effective_runtime_config.py")])

        if args.check_only:
            print("[startup-check] CHECK_ONLY=PASS; web server not started")
            return 0

        host = os.environ.get("STORAGE_WEB_HOST", "0.0.0.0")
        port = int(os.environ.get("STORAGE_WEB_PORT", "8765"))
        print(f"[startup] Storage web server: http://127.0.0.1:{port}")
        print("[startup] Waiting for user/business requests. No automatic E2E request will be sent.")
        print("[startup] Press Ctrl+C to stop.")

        import uvicorn

        uvicorn.run("storage_life.app:app", host=host, port=port, log_level="info")
        return 0
    except KeyboardInterrupt:
        print("[startup] stopped by user")
        return 0
    finally:
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
