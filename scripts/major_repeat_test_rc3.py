"""Start or verify the RC3 package without pre-seeding any published case."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def smoke() -> int:
    command = [
        sys.executable, "-m", "pytest", "-q",
        "tests/test_major_repeat_gp01_browser_production.py",
        "tests/test_case_publish_service.py",
        "tests/test_golden_e2e_001.py",
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        return completed.returncode
    print("CLEAN_STATE_GP01=PASS")
    print("PRESEEDED_PUBLISHED_HISTORY=NO")
    return 0


def serve(db_path: Path, host: str, port: int) -> int:
    import uvicorn
    from quality_knowledge.p0.initializer import P0Initializer
    from quality_knowledge.web.p0_app import create_p0_app

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
    uvicorn.run(create_p0_app(db_path, project_root=ROOT), host=host, port=port)
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
