"""Start or verify RC5 with the formal package-only Major mock provider."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FORMAL_MOCK_FIXTURE_ID = "MAJOR_REPEAT_V11_F02"
FORMAL_MOCK_FIXTURE = {
    "ISSUE_FACT": "设备在特定负载切换条件下偶发母线过压并触发保护停机",
    "ROOT_CAUSE": "负载突变下回馈能量释放路径与控制参数组合导致母线电压瞬态上升",
    "ACTION": "优化制动/回馈策略及相关控制参数，并补充边界工况验证",
    "VERIFICATION": "重复边界负载切换后未再复现母线过压保护",
}


def formal_mock_provider(_provider_input, pending_specs, _context):
    """Return fixture-backed Provider Responses; never create business state."""
    responses = []
    for spec in pending_specs:
        unit_id = str(spec["unit_id"])
        if unit_id not in FORMAL_MOCK_FIXTURE:
            raise ValueError(f"UNSUPPORTED_MAJOR_PROVIDER_UNIT:{unit_id}")
        responses.append({
            "object_id": spec["object_id"],
            "data": {"content": FORMAL_MOCK_FIXTURE[unit_id]},
        })
    return responses


def create_package_app(db_path: Path):
    """Compose the formal TEST_RC app with its package-only mock boundary."""
    from quality_knowledge.web.p0_app import create_p0_app

    return create_p0_app(
        db_path,
        project_root=ROOT,
        major_provider=formal_mock_provider,
    )


def smoke() -> int:
    command = [
        sys.executable, "-m", "pytest", "-q",
        "tests/test_major_repeat_gp01_browser_production.py",
        "tests/test_major_repository_connection_lifecycle.py",
        "tests/test_major_repeat_rc5_provider_wiring.py",
        "tests/test_case_publish_service.py",
        "tests/test_golden_e2e_001.py",
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        return completed.returncode
    print("CLEAN_STATE_GP01=PASS")
    print("PRESEEDED_PUBLISHED_HISTORY=NO")
    print("MAJOR_PROVIDER_CONFIGURED=PASS")
    print("MAJOR_PROVIDER_MODE=MOCK")
    print("PROVIDER_BOUNDARY=PROVIDER_RESPONSE_ONLY")
    print("FROZEN_FIXTURE=MAJOR_REPEAT_V11_F02")
    return 0


def serve(db_path: Path, host: str, port: int) -> int:
    import uvicorn
    from quality_knowledge.p0.initializer import P0Initializer

    initializer = P0Initializer(
        manifest_path=ROOT / "quality_knowledge/config/p0_seed_manifest.json",
        plc_seed_path=ROOT / "quality_knowledge/config/plc_fields.yaml",
    )
    if db_path.exists():
        initializer.verify_ready(db_path)
    else:
        initializer.initialize(db_path)
    print(f"WEB_URL=http://{host}:{port}/p0/major-production")
    print("CLEAN_STATE=YES")
    print("TARGET_ENV=PENDING")
    print("MVP_READY=NO")
    print("MAJOR_PROVIDER_CONFIGURED=PASS")
    print("MAJOR_PROVIDER_MODE=MOCK")
    print("PROVIDER_BOUNDARY=PROVIDER_RESPONSE_ONLY")
    print("FROZEN_FIXTURE=MAJOR_REPEAT_V11_F02")
    uvicorn.run(create_package_app(db_path), host=host, port=port)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "smoke"))
    parser.add_argument("--db", type=Path, default=ROOT.parent / "data/runtime/quality_capability_p0.sqlite3")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    return smoke() if args.action == "smoke" else serve(args.db, args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
