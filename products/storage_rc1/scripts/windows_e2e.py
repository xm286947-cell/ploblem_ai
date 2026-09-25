from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = "STORAGE_NEXT_001_WINDOWS_REAL_VALIDATION_20260922_R41"
EXPECTED_RUNTIME = "f9ca45f82960b3ce380273cf26868bc842a72b7f"
SNAPSHOT_SOURCE = "14e22c1bec3900e5b86b6c8c339852f9190db86d"


class Tee:
    def __init__(self, session_path: Path, latest_path: Path):
        self.session_path = session_path
        self.latest_path = latest_path
        self.lock = threading.Lock()
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text("", encoding="utf-8")
        latest_path.write_text("", encoding="utf-8")

    def write(self, text: str, *, console: bool = True) -> None:
        with self.lock:
            for path in (self.session_path, self.latest_path):
                with path.open("a", encoding="utf-8", errors="replace") as f:
                    f.write(text)
                    f.flush()
            if console:
                try:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                except UnicodeEncodeError:
                    try:
                        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
                        safe = text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
                        sys.stdout.write(safe)
                        sys.stdout.flush()
                    except Exception:
                        pass


def wait_url(url: str, *, timeout: float, name: str, tee: Tee) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                resp.read(32)
                tee.write(f"[health] {name}: PASS {url}\n")
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.25)
    raise RuntimeError(f"{name} health check failed: {url}: {last}")


def spawn_logged(
    cmd: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    component_log: Path,
    tee: Tee,
    label: str,
) -> tuple[subprocess.Popen[str], threading.Thread]:
    component_log.parent.mkdir(parents=True, exist_ok=True)
    component_log.write_text("", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def pump() -> None:
        assert proc.stdout is not None
        with component_log.open("a", encoding="utf-8", errors="replace") as out:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                tee.write(f"[{label}] {line}")

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return proc, thread


def run_logged(
    cmd: list[str],
    *,
    env: dict[str, str],
    tee: Tee,
    capture_path: Path | None = None,
) -> int:
    tee.write("[exec] " + " ".join(cmd) + "\n")
    if capture_path is not None:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_text("", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    out_handle = (
        capture_path.open("a", encoding="utf-8", errors="replace")
        if capture_path is not None
        else None
    )
    try:
        for line in proc.stdout:
            if out_handle is not None:
                out_handle.write(line)
                out_handle.flush()
            tee.write(line)
    finally:
        if out_handle is not None:
            out_handle.close()
    return proc.wait()


def tail_text(path: Path, lines: int = 400) -> str:
    if not path.exists():
        return "<not created>\n"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return f"<read failed: {type(exc).__name__}: {exc}>\n"
    return "\n".join(content[-lines:]) + ("\n" if content else "")


def _provider_trace_state(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "NO", "provider trace file was not created"
    text = path.read_text(encoding="utf-8", errors="replace")
    if '"phase": "request"' not in text:
        return "NO", "Runtime Provider Adapter was not reached"
    if '"phase": "response"' in text:
        return "YES", "provider request reached endpoint and returned an HTTP response"
    if '"phase": "http_error"' in text:
        return "YES", "provider request reached endpoint and returned HTTP error"
    if '"phase": "transport_error"' in text:
        return "YES", "provider request left Runtime and failed at transport layer"
    return "YES", "provider request was emitted; no terminal trace event was recorded"


def collect_diagnostics(
    session_log: Path,
    *,
    outcome: str,
    error: BaseException | str | None = None,
) -> Path:
    logs = ROOT / "logs"
    provider_trace = logs / "provider_runtime.log"
    effective = logs / "effective_runtime_config.json"
    reached, interpretation = _provider_trace_state(provider_trace)
    out = logs / "diagnostic_latest.txt"
    parts = [
        "STORAGE REAL AGENT DIAGNOSTIC BUNDLE\n",
        f"package={PACKAGE_VERSION}\n",
        f"generated_at={datetime.now().isoformat(timespec='seconds')}\n",
        f"outcome={outcome}\n",
        f"runtime_commit={EXPECTED_RUNTIME}\n",
        f"snapshot_source_commit={SNAPSHOT_SOURCE}\n",
        f"provider_request_reached={reached}\n",
        f"provider_trace_interpretation={interpretation}\n",
    ]
    if error is not None:
        if isinstance(error, BaseException):
            parts.append(f"error={type(error).__name__}: {error}\n")
        else:
            parts.append(f"error={error}\n")
    parts.append("\n")

    for path, label, lines in [
        (effective, "EFFECTIVE RUNTIME CONFIG", 250),
        (provider_trace, "RUNTIME PROVIDER REQUEST TRACE", 400),
        (session_log, "SESSION LOG TAIL", 250),
        (ROOT / "release" / "storage_app.log", "STORAGE / RUNTIME LOG TAIL", 250),
    ]:
        parts.append(f"===== {label}: {path} =====\n")
        parts.append(tail_text(path, lines=lines))
        parts.append("\n")

    out.write_text("".join(parts), encoding="utf-8")
    return out


def collect_failure(session_log: Path, error: BaseException | str) -> Path:
    diagnostic = collect_diagnostics(session_log, outcome="FAIL", error=error)
    out = ROOT / "logs" / "failure_latest.txt"
    shutil.copyfile(diagnostic, out)
    return out


def _env_flag(env: dict[str, str], name: str) -> bool:
    return str(env.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _same_path(left: Path | str, right: Path | str) -> bool:
    try:
        left_text = os.path.normcase(str(Path(left).expanduser().resolve()))
        right_text = os.path.normcase(str(Path(right).expanduser().resolve()))
        return left_text == right_text
    except Exception:  # noqa: BLE001
        return False


def _is_under(path: Path | str, root: Path | str) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).expanduser().resolve())
        return True
    except Exception:  # noqa: BLE001
        return False


def _expected_package_python() -> Path:
    if os.name == "nt":
        return (ROOT / ".venv" / "Scripts" / "python.exe").resolve()
    return (ROOT / ".venv" / "bin" / "python").resolve()


def _other_product_package(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if parent.name.upper().startswith("STORAGE_PRODUCT_TEST_FULL_V"):
            if not _same_path(parent, ROOT):
                return parent
            return None
    return None


def verify_process_provenance(tee: Tee, env: dict[str, str]) -> None:
    strict = _env_flag(env, "STORAGE_STRICT_PACKAGE_PROVENANCE")
    expected_python = _expected_package_python()
    actual_python = Path(sys.executable).resolve()
    actual_cwd = Path.cwd().resolve()
    virtual_env_raw = str(env.get("VIRTUAL_ENV") or "").strip()
    virtual_env = Path(virtual_env_raw).resolve() if virtual_env_raw else None

    tee.write(f"[provenance] strict={strict}\n")
    tee.write(f"[provenance] package_root={ROOT}\n")
    tee.write(f"[provenance] working_dir={actual_cwd}\n")
    tee.write(f"[provenance] python_executable={actual_python}\n")
    tee.write(f"[provenance] expected_python={expected_python}\n")
    tee.write(f"[provenance] virtual_env={virtual_env or '<unset>'}\n")

    if not strict:
        return

    errors: list[str] = []
    if not _same_path(actual_cwd, ROOT):
        errors.append(f"working_dir expected={ROOT} actual={actual_cwd}")
    if not _same_path(actual_python, expected_python):
        errors.append(f"python expected={expected_python} actual={actual_python}")
    expected_venv = (ROOT / ".venv").resolve()
    if virtual_env is None or not _same_path(virtual_env, expected_venv):
        errors.append(f"VIRTUAL_ENV expected={expected_venv} actual={virtual_env or '<unset>'}")
    if errors:
        raise RuntimeError("PROVENANCE_GATE_PROCESS_FAILED: " + "; ".join(errors))
    tee.write("[provenance] process_gate=PASS\n")


def verify_runtime(tee: Tee, *, strict: bool = False) -> Path:
    bundled = (ROOT / "vendor" / "unified_agent_runtime").resolve()
    root = Path(
        os.environ.get("UNIFIED_AGENT_RUNTIME_ROOT")
        or bundled
    ).resolve()
    if strict and not _same_path(root, bundled):
        raise RuntimeError(
            f"PROVENANCE_GATE_RUNTIME_FAILED: expected bundled Runtime={bundled} actual={root}"
        )
    marker = root / "RUNTIME_COMMIT"
    source_marker = root / "SNAPSHOT_SOURCE_COMMIT"
    if not (root / "runtime" / "__init__.py").is_file():
        raise RuntimeError(f"Bundled Runtime missing: {root}")
    if not marker.is_file():
        raise RuntimeError(f"Runtime commit marker missing: {marker}")
    actual = marker.read_text(encoding="utf-8").strip()
    if actual != EXPECTED_RUNTIME:
        raise RuntimeError(
            f"Runtime snapshot mismatch expected={EXPECTED_RUNTIME} actual={actual}"
        )
    source = source_marker.read_text(encoding="utf-8").strip() if source_marker.is_file() else "<missing>"
    tee.write(
        f"[provenance] runtime_root={root}\n"
        f"RUNTIME_COMMIT={actual}\n"
        f"RUNTIME_SNAPSHOT_SOURCE={source}\n"
    )
    if strict:
        tee.write("[provenance] runtime_gate=PASS\n")
    return root


def stop_process(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=4)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def resolve_model_config(env: dict[str, str], tee: Tee) -> Path:
    configured = str(env.get("STORAGE_MODEL_CONFIG") or "").strip()
    source_hint = str(env.get("STORAGE_MODEL_CONFIG_SOURCE") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        else:
            path = path.resolve()
        source = source_hint or "user"
    else:
        path = (ROOT / "config" / "model.local.yaml").resolve()
        source = "package-default"
    if not path.is_file():
        raise RuntimeError(f"model config missing: {path}")
    if _env_flag(env, "STORAGE_STRICT_PACKAGE_PROVENANCE"):
        foreign_package = _other_product_package(path)
        if foreign_package is not None:
            raise RuntimeError(
                "PROVENANCE_GATE_MODEL_CONFIG_FAILED: "
                f"model config belongs to another package {foreign_package}"
            )
    env["STORAGE_MODEL_CONFIG"] = str(path)
    env["STORAGE_MODEL_CONFIG_SOURCE"] = source
    tee.write(f"[config] source={source}\n")
    tee.write(f"[config] effective STORAGE_MODEL_CONFIG={path}\n")
    return path


def apply_local_provider_no_proxy(
    model_config: Path,
    env: dict[str, str],
    tee: Tee,
) -> None:
    try:
        import yaml

        raw = yaml.safe_load(model_config.read_text(encoding="utf-8")) or {}
        models = raw.get("models") or {}
        active = str(raw.get("active_model") or "qwen_prod")
        profile = models.get(active) or models.get("qwen_prod") or {}
        base = str(profile.get("base_url") or "").strip()
        host = urlsplit(base).hostname
        if not host:
            return
        direct = host.lower() == "localhost"
        try:
            address = ipaddress.ip_address(host)
            direct = direct or address.is_private or address.is_loopback
        except ValueError:
            pass
        if not direct:
            return
        for name in ("NO_PROXY", "no_proxy"):
            current = env.get(name, "")
            parts = [x.strip() for x in current.split(",") if x.strip()]
            for item in (host, "127.0.0.1", "localhost"):
                if item not in parts:
                    parts.append(item)
            env[name] = ",".join(parts)
        tee.write(f"[config] direct local/private provider; NO_PROXY includes {host}\n")
    except Exception as exc:  # noqa: BLE001
        tee.write(
            f"[config] NO_PROXY setup warning: {type(exc).__name__}: {exc}\n"
        )


def write_effective_runtime_config(env: dict[str, str], tee: Tee) -> Path:
    out = ROOT / "logs" / "effective_runtime_config.json"
    cmd = [sys.executable, "scripts/effective_runtime_config.py"]
    rc = run_logged(cmd, env=env, tee=tee)
    if rc != 0:
        raise RuntimeError(f"effective runtime config check failed rc={rc}")
    if not out.is_file():
        raise RuntimeError(f"effective runtime config log missing: {out}")
    return out


def verify_effective_provenance(
    effective_path: Path,
    *,
    model_config: Path,
    runtime_root: Path,
    env: dict[str, str],
    tee: Tee,
) -> None:
    data = json.loads(effective_path.read_text(encoding="utf-8"))
    strict = _env_flag(env, "STORAGE_STRICT_PACKAGE_PROVENANCE")
    config_source = str(env.get("STORAGE_MODEL_CONFIG_SOURCE") or "user")
    expected_agents = {
        "storage.ai.json_call": (ROOT / "config" / "runtime" / "storage.ai.json_call.yaml").resolve(),
        "storage.emmc.parameter_extract": (ROOT / "config" / "runtime" / "storage.emmc.parameter_extract.yaml").resolve(),
    }

    checks = {
        "package_root": _same_path(data.get("package_root") or "", ROOT),
        "working_dir": _same_path(data.get("working_dir") or "", ROOT),
        "runtime_root": _same_path(data.get("runtime_root") or "", runtime_root),
        "runtime_module": _same_path(
            data.get("runtime_module") or "", runtime_root / "runtime" / "__init__.py"
        ),
        "runtime_provider_adapter": _same_path(
            data.get("runtime_provider_adapter") or "",
            runtime_root / "runtime" / "providers" / "openai_compatible.py",
        ),
        "storage_runtime_bridge_module": _same_path(
            data.get("storage_runtime_bridge_module") or "",
            ROOT / "storage_life" / "runtime_bridge.py",
        ),
        "runtime_model_config": _same_path(data.get("runtime_model_config") or "", model_config),
        "storage_model_config_env": _same_path(data.get("storage_model_config_env") or "", model_config),
    }
    agent_configs = data.get("agent_configs") or {}
    for agent_id, expected in expected_agents.items():
        checks[f"agent_config:{agent_id}"] = _same_path(agent_configs.get(agent_id) or "", expected)

    if strict:
        checks["python_executable"] = _same_path(
            data.get("python_executable") or "", _expected_package_python()
        )
        checks["bundled_runtime"] = _same_path(
            runtime_root, ROOT / "vendor" / "unified_agent_runtime"
        )
        if config_source == "package-default":
            checks["package_default_model_config"] = _same_path(
                model_config, ROOT / "config" / "model.local.yaml"
            )

    tee.write(f"[provenance] model_config={model_config} source={config_source}\n")
    for agent_id, expected in expected_agents.items():
        tee.write(f"[provenance] agent_config[{agent_id}]={agent_configs.get(agent_id) or '<missing>'}\n")
    tee.write(f"[provenance] runtime_module={data.get('runtime_module')}\n")
    tee.write(f"[provenance] storage_runtime_bridge={data.get('storage_runtime_bridge_module')}\n")
    tee.write(f"[provenance] provider_adapter={data.get('runtime_provider_adapter')}\n")

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        detail = ", ".join(failed)
        raise RuntimeError(f"PROVENANCE_GATE_EFFECTIVE_FAILED: {detail}")
    tee.write("[provenance] effective_gate=PASS\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "real"], default="real")
    args = ap.parse_args()

    logs = ROOT / "logs"
    release = ROOT / "release"
    logs.mkdir(exist_ok=True)
    release.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_log = logs / f"windows_{args.mode}_{stamp}.log"
    latest_log = logs / "latest.log"
    provider_trace = logs / "provider_runtime.log"
    tee = Tee(session_log, latest_log)

    for p in [
        release / "storage_app.log",
        release / "openai_mock.log",
        release / "mock_router.log",
        release / "PRODUCT_E2E_RESULT.json",
        release / "PRODUCT_FAILURE_E2E_RESULT.json",
    ]:
        if p.suffix == ".log":
            p.write_text("", encoding="utf-8")
        elif p.exists():
            p.unlink()
    for p in [
        logs / "failure_latest.txt",
        logs / "diagnostic_latest.txt",
        logs / "effective_runtime_config.json",
    ]:
        if p.exists():
            p.unlink()
    # Guarantee a provider trace file exists even when execution fails before
    # Runtime Provider Adapter is reached.
    provider_trace.write_text(
        f"[trace-init] {datetime.now().isoformat(timespec='milliseconds')} {PACKAGE_VERSION}\n",
        encoding="utf-8",
    )

    tee.write(
        f"{PACKAGE_VERSION}\n"
        "STORAGE WINDOWS E2E\n"
        f"MODE={args.mode}\n"
        f"STARTED_AT={datetime.now().isoformat(timespec='seconds')}\n"
        f"PROVIDER_TRACE_FILE={provider_trace}\n"
    )

    processes: list[subprocess.Popen[str]] = []
    try:
        env = os.environ.copy()
        strict_provenance = _env_flag(env, "STORAGE_STRICT_PACKAGE_PROVENANCE")
        verify_process_provenance(tee, env)
        runtime_root = verify_runtime(tee, strict=strict_provenance)
        env["UNIFIED_AGENT_RUNTIME_ROOT"] = str(runtime_root)
        if strict_provenance:
            # Do not inherit stale V1.0/project PYTHONPATH entries in packaged Windows E2E.
            env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(runtime_root)])
        else:
            inherited_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                [x for x in [str(ROOT), str(runtime_root), inherited_pythonpath] if x]
            )
        env["STORAGE_LIFE_EXECUTION_MODE"] = "runtime"
        env["STORAGE_PRODUCT_TEST_MODE"] = args.mode
        # Do not allow caller defaults to silently turn diagnostics off.
        env["RUNTIME_PROVIDER_TRACE"] = "1"
        env["RUNTIME_PROVIDER_DIAGNOSTICS"] = "1"
        env["RUNTIME_PROVIDER_TRACE_FILE"] = str(provider_trace.resolve())
        env["STORAGE_RUNTIME_HTTP_TRACE"] = "1"
        env["STORAGE_APP_LOG_STDOUT"] = "1"
        env.setdefault("STORAGE_WEB_HOST", "0.0.0.0")
        env.setdefault("STORAGE_WEB_PORT", "8765")

        fault = env.get("STORAGE_MOCK_FAULT", "normal")
        if args.mode == "mock":
            env.pop("STORAGE_MODEL_CONFIG", None)
            env["DASHSCOPE_BASE_URL"] = "http://127.0.0.1:18001/v1"
            env["DASHSCOPE_API_KEY"] = "mock-key"
        else:
            model_config = resolve_model_config(env, tee)
            apply_local_provider_no_proxy(model_config, env, tee)
            effective_path = write_effective_runtime_config(env, tee)
            verify_effective_provenance(
                effective_path,
                model_config=model_config,
                runtime_root=runtime_root,
                env=env,
                tee=tee,
            )
            tee.write("[provider] direct_probe=SKIPPED; network_owner=Unified Agent Runtime\n")

        rc = run_logged(
            [
                sys.executable,
                "scripts/preflight.py",
                "--runtime-root",
                str(runtime_root),
                "--mode",
                args.mode,
            ],
            env=env,
            tee=tee,
        )
        if rc != 0:
            raise RuntimeError(f"preflight failed rc={rc}")

        data_dir = Path(
            env.get("STORAGE_LIFE_DATA_DIR")
            or (ROOT / ".testdata" / f"{args.mode}-{fault}")
        )
        runtime_db = Path(
            env.get("STORAGE_LIFE_RUNTIME_DB")
            or (ROOT / ".testdata" / f"{args.mode}-{fault}-runtime.sqlite3")
        )
        env["STORAGE_LIFE_DATA_DIR"] = str(data_dir)
        env["STORAGE_LIFE_RUNTIME_DB"] = str(runtime_db)
        if env.get("STORAGE_TEST_RESET_DATA", "1") == "1":
            shutil.rmtree(data_dir, ignore_errors=True)
            try:
                runtime_db.unlink()
            except FileNotFoundError:
                pass
        data_dir.mkdir(parents=True, exist_ok=True)

        if args.mode == "mock":
            mock, _ = spawn_logged(
                [
                    sys.executable,
                    "-m",
                    "tools.openai_mock.server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18000",
                ],
                env=env,
                cwd=ROOT,
                component_log=release / "openai_mock.log",
                tee=tee,
                label="OPENAI-MOCK",
            )
            processes.append(mock)
            wait_url(
                "http://127.0.0.1:18000/__mock__/health",
                timeout=20,
                name="OPENAI-MOCK",
                tee=tee,
            )

            router_env = env.copy()
            router_env["OPENAI_MOCK_UPSTREAM"] = "http://127.0.0.1:18000"
            router, _ = spawn_logged(
                [sys.executable, "test_support/mock_router.py", "--port", "18001"],
                env=router_env,
                cwd=ROOT,
                component_log=release / "mock_router.log",
                tee=tee,
                label="MOCK-ROUTER",
            )
            processes.append(router)
            wait_url(
                "http://127.0.0.1:18001/health",
                timeout=20,
                name="Storage-Mock-Router",
                tee=tee,
            )

        port = env["STORAGE_WEB_PORT"]
        app, _ = spawn_logged(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "storage_life.app:app",
                "--host",
                env["STORAGE_WEB_HOST"],
                "--port",
                port,
            ],
            env=env,
            cwd=ROOT,
            component_log=release / "storage_app.log",
            tee=tee,
            label="STORAGE-APP",
        )
        processes.append(app)
        base = f"http://127.0.0.1:{port}"
        wait_url(base + "/api/health", timeout=30, name="Storage-Web", tee=tee)

        if fault == "persistent_503":
            cmd = [
                sys.executable,
                "scripts/product_failure_e2e.py",
                "--base",
                base,
                "--timeout",
                env.get("STORAGE_PRODUCT_E2E_TIMEOUT", "180"),
            ]
        else:
            timeout = env.get(
                "STORAGE_PRODUCT_E2E_TIMEOUT",
                "360" if args.mode == "real" else "120",
            )
            cmd = [
                sys.executable,
                "scripts/product_e2e.py",
                "--base",
                base,
                "--timeout",
                timeout,
            ]
        rc = run_logged(cmd, env=env, tee=tee)
        if rc != 0:
            raise RuntimeError(f"product E2E failed rc={rc}")

        tee.write("\nPRODUCT E2E PASS\n")
        diagnostic = collect_diagnostics(session_log, outcome="PASS")
        tee.write(f"Diagnostic={diagnostic}\n")
        tee.write(f"ProviderTrace={provider_trace}\n")
        tee.write(f"Web=http://127.0.0.1:{port}\n")
        tee.write(f"SessionLog={session_log}\n")
        tee.write(f"StorageLog={release / 'storage_app.log'}\n")

        no_wait = env.get("STORAGE_TEST_NO_WAIT", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not no_wait:
            tee.write(
                "Environment remains running for manual web checks. "
                "Press ENTER to stop.\n"
            )
            try:
                input()
            except EOFError:
                pass
        return 0
    except KeyboardInterrupt:
        bundle = collect_failure(session_log, "Interrupted by user")
        tee.write(f"\nINTERRUPTED. Failure bundle: {bundle}\n")
        return 130
    except BaseException as exc:  # noqa: BLE001
        tee.write(f"\nPRODUCT E2E FAILED: {type(exc).__name__}: {exc}\n")
        bundle = collect_failure(session_log, exc)
        tee.write(f"Failure bundle: {bundle}\n")
        tee.write(f"Provider trace: {provider_trace}\n")
        return 3
    finally:
        for proc in reversed(processes):
            stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
