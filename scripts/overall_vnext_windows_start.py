"""Windows launcher for the single-host Overall VNext manual-trial package."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "scripts" / "overall_vnext_demo.py"


def default_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "OverallVNextDemo" / "data"


def verify_runtime_binding(package_root: Path) -> dict[str, str]:
    """Verify manifest pin and exact Runtime import source before host startup."""
    root = package_root.expanduser().resolve()
    manifest_path = root / "OVERALL_VNEXT_WINDOWS_TRIAL_MANIFEST.json"
    if not manifest_path.is_file():
        raise RuntimeError("OVERALL_VNEXT_TRIAL_MANIFEST_MISSING")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("OVERALL_VNEXT_TRIAL_MANIFEST_INVALID") from exc
    if manifest.get("package_type") != "WINDOWS_MANUAL_TRIAL_PACKAGE":
        raise RuntimeError("OVERALL_VNEXT_TRIAL_PACKAGE_TYPE_INVALID")
    expected_commit = str(manifest.get("dut_source_commit") or "").strip()
    if not expected_commit:
        raise RuntimeError("OVERALL_VNEXT_DUT_COMMIT_MISSING")

    actual_commit = None
    if (root / ".git").exists():
        try:
            actual_commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                text=True, capture_output=True, timeout=5, check=True,
            ).stdout.strip()
        except Exception:
            actual_commit = None
    marker = root / "RUNTIME_COMMIT"
    if not actual_commit and marker.is_file():
        actual_commit = marker.read_text(encoding="utf-8").strip()
    if actual_commit != expected_commit:
        raise RuntimeError(
            "OVERALL_VNEXT_DUT_COMMIT_MISMATCH:"
            f"expected={expected_commit};actual={actual_commit or 'unverified'}"
        )

    expected_module = (root / "runtime" / "__init__.py").resolve()
    if not expected_module.is_file():
        raise RuntimeError("SHARED_RUNTIME_SOURCE_MISSING")
    vendor_module = root / "vendor" / "unified_agent_runtime" / "runtime" / "__init__.py"
    if vendor_module.exists():
        raise RuntimeError("DUPLICATE_VENDOR_RUNTIME_PRESENT")

    root_text = str(root)
    if root_text in sys.path:
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)

    import runtime

    actual_module = Path(runtime.__file__).resolve()
    if actual_module != expected_module:
        raise RuntimeError(
            "RUNTIME_IMPORT_SOURCE_MISMATCH:"
            f"expected={expected_module};actual={actual_module}"
        )
    return {
        "runtime_expected_commit": expected_commit,
        "runtime_actual_commit": actual_commit,
        "runtime_expected_source": str(expected_module),
        "runtime_actual_source": str(actual_module),
    }


def wait_ready(url: str, process: subprocess.Popen[bytes], timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"APPLICATION_EXITED_EARLY:{process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"APPLICATION_START_TIMEOUT:{last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--package-root", type=Path, default=ROOT)
    parser.add_argument("--check-runtime-only", action="store_true")
    args = parser.parse_args()

    package_root = args.package_root.expanduser().resolve()
    try:
        runtime_info = verify_runtime_binding(package_root)
    except Exception as exc:
        print(f"RUNTIME_BINDING=FAIL:{exc}", file=sys.stderr)
        return 6
    print("RUNTIME_BINDING=PASS")
    for key, value in runtime_info.items():
        print(f"{key.upper()}={value}")
    if args.check_runtime_only:
        return 0

    args.data_dir = args.data_dir.expanduser().resolve()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = args.data_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "launcher.log"
    command = [
        sys.executable, str(package_root / "scripts" / "overall_vnext_demo.py"),
        "--data-dir", str(args.data_dir), "--host", args.host, "--port", str(args.port),
    ]
    if args.check_only:
        return subprocess.run(command + ["--check"], cwd=package_root, check=False).returncode

    url = f"http://{args.host}:{args.port}/p0/issues"
    print("OVERALL_VNEXT_STARTUP=SERVER_ONLY")
    print("DATA_MODE=SYNTHETIC_ISOLATED")
    print("PROVIDER_CALL_ON_STARTUP=NO")
    print(f"DATA_DIR={args.data_dir}")
    print(f"LOG={log_path}")
    print(f"URL={url}")
    print("Press Ctrl+C in this window to stop the application.")

    with log_path.open("a", encoding="utf-8", errors="replace", buffering=1) as log:
        for key, value in runtime_info.items():
            log.write(f"{key.upper()}={value}\n")
        process = subprocess.Popen(command, cwd=package_root, stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_ready(url, process)
            print("STARTUP=PASS")
            print(f"OPENING={url}")
            webbrowser.open(url)
            return process.wait()
        except KeyboardInterrupt:
            print("STOPPING=USER_REQUEST")
            return 0
        except Exception as exc:
            print(f"STARTUP=FAIL:{exc}")
            print(f"See log: {log_path}")
            return 5
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
