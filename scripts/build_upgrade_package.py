from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
BASE_TAG = "v1.1-p2-rc2-full-20260901"
PATCH_VERSION = "V1.1_P2_RC2_PATCH01_20260902"
OUTPUT = ROOT / "baseline_release"
PACKAGE_ROOT = f"KNOWLEDGE_QUALITY_ISSUE_ANALYSIS_ENGINE_{PATCH_VERSION}"
EXCLUDED_PREFIXES = ("knowledge/raw_evidence/", "knowledge/raw_excel/", "output/", "baseline_release/", "releases/")
EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log", ".zip", ".pyc")
REQUIRED_WORKBENCH_FILES = {
    "quality_knowledge/materials.py",
    "quality_knowledge/web/app.py",
    "quality_knowledge/web/static/app.css",
    "quality_knowledge/web/templates/base.html",
    "quality_knowledge/web/templates/materials.html",
    "quality_knowledge/web/templates/material_detail.html",
}


def changed_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", f"{BASE_TAG}..HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    names = set(result.stdout.splitlines()) | REQUIRED_WORKBENCH_FILES
    files = []
    for name in names:
        if not name or name.startswith(EXCLUDED_PREFIXES) or name.endswith(EXCLUDED_SUFFIXES):
            continue
        path = ROOT / name
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(ROOT).as_posix())


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    files = changed_files()
    manifest = {
        "patch_version": PATCH_VERSION,
        "base_tag": BASE_TAG,
        "target_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "file_count": len(files),
        "files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in files
        ],
    }
    readme = """# PATCH01 升级说明

适用基线：V1.1_P2_RC2_FULL_20260901

升级内容：
- 修复 Windows 导入 ITR Excel 后文件句柄未释放的问题。
- 将 ITR、彻底解决单和软件考核页升级为可检索、筛选、分页和查看详情的工作台。
- 增加完整原始字段、同 ITR 关联数据、漏测分析跳转和独立人工分析。
- 归档产品质量场景库设计，尚未开发独立 AI 场景推理。

升级后工作台入口：
- ITR问题工作台：`/materials/itr`
- ITR彻底解决工作台：`/materials/cs`
- 软件问题考核工作台：`/materials/software-operations`

本包已显式包含上述三个工作台所需的侧边栏入口、路由、数据服务、列表页、详情页和样式文件。

升级步骤：
1. 停止 BAT 启动的服务。
2. 备份当前程序目录和正在使用的 SQLite 数据库文件。
3. 将本升级包内容覆盖到原程序根目录，保持目录结构不变。
4. 使用原 BAT 重新启动。
5. 依次打开 ITR工作台、彻底解决工作台和软件考核工作台验证。

升级包不包含、不覆盖数据库、原始Excel、日志和运行输出。新增数据表在启动工作台时自动创建。
"""
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
    archive_path = OUTPUT / f"{PACKAGE_ROOT}.zip"
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{PACKAGE_ROOT}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(f"{PACKAGE_ROOT}/UPGRADE_MANIFEST.json", manifest_text)
        archive.writestr(f"{PACKAGE_ROOT}/UPGRADE_README.md", readme)
    (OUTPUT / f"{PATCH_VERSION}_MANIFEST.json").write_text(manifest_text, encoding="utf-8")
    print(archive_path)
    print(f"files={len(files)}")


if __name__ == "__main__":
    main()
