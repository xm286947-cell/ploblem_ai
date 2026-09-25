"""Build the clean-state MAJOR_REPEAT_PRODUCT_MVP_TEST_RC3 package."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_major_repeat_test_rc2 as base


NAME = "MAJOR_REPEAT_PRODUCT_MVP_TEST_RC3"
TESTS = (
    "test_major_repeat_gp01_browser_production.py",
    "test_case_publish_service.py",
    "test_golden_e2e_001.py",
)


def _stage_app(stage: Path) -> None:
    original(stage)
    (stage / "evidence").mkdir(parents=True, exist_ok=True)
    old = stage / "app/scripts/major_repeat_test_rc1.py"
    old.unlink(missing_ok=True)
    base.copy(ROOT / "scripts/major_repeat_test_rc3.py", stage / "app/scripts/major_repeat_test_rc3.py")
    source = ROOT / "tests/golden/hardware_case_scenarios/A9001-LDO 输出振荡.docx"
    base.copy(source, stage / "app/tests/golden/hardware_case_scenarios" / source.name)
    base.copy(ROOT / "docs/product/major_repeat_test_rc3/RC3_TEST_SCOPE.md", stage / "docs/RC3_TEST_SCOPE.md")


def _copy(source: Path, target: Path) -> None:
    # The inherited builder expects legacy Windows helpers.  RC3 deliberately
    # maps its launcher to the clean-state implementation and omits old test
    # helper entry points which seeded historical records.
    if source.name in {"smoke_test.bat", "golden_test.bat"} and "major_repeat_test_rc1" in source.as_posix():
        return
    if source.name == "run_windows.bat" and "major_repeat_test_rc1" in source.as_posix():
        source = ROOT / "packaging/major_repeat_test_rc3/run_windows.bat"
    if source.name in {"run_linux.sh", "run_macos.sh", "run_posix.sh"} and "major_repeat_test_rc2" in source.as_posix():
        source = ROOT / "packaging/major_repeat_test_rc3" / source.name
    original_copy(source, target)


def _rewrite_manifest(archive: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="major-repeat-rc3-manifest-") as directory:
        directory_path = Path(directory)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(directory_path)
        stage = directory_path / NAME
        manifest_path = stage / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({
            "package": NAME,
            "package_type": "TEST_RC",
            "source_commit": base.git("rev-parse", "HEAD"),
            "base_commit": base.git("rev-parse", "origin/main"),
            "source_main": base.git("rev-parse", "origin/main"),
            "product_stage": "MVP_INTEGRATION",
            "engineering_golden": "PASS",
            "target_env": "PENDING",
            "mvp_ready": False,
            "clean_state_browser_flow": "GP01",
            "preseeded_published_history": False,
        })
        manifest["files"] = []
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        base.scan(stage)
        files = [
            {"path": path.relative_to(stage).as_posix(), "sha256": base.sha256(path), "size": path.stat().st_size}
            for path in sorted(stage.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"
        ]
        manifest["files"] = files
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sums = [
            f"{base.sha256(path)}  {path.relative_to(stage).as_posix()}"
            for path in sorted(stage.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"
        ]
        (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
        base.scan(stage)
        base.write_zip(stage, archive)
    digest = base.sha256(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    global original, original_copy
    original = base.stage_app
    original_copy = base.copy
    base.NAME = NAME
    base.TESTS = TESTS
    base.SCREENSHOTS = ()
    base.RC1_DOCS = ()
    base.RC2_DOCS = ()
    base.stage_app = _stage_app
    base.copy = _copy
    archive, _ = base.build(args.out_dir.resolve(), ROOT / "tests")
    digest = _rewrite_manifest(archive)
    print(f"PACKAGE={archive}")
    print(f"SHA256={digest}")
    print("PACKAGE_BUILD=PASS")
    print("SECRET_SCAN=PASS")
    print("MVP_READY=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
