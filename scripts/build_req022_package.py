"""Build the data-free REQ-022 cumulative upgrade package."""
from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import hashlib
import json


ROOT = Path(__file__).resolve().parents[1]
VERSION = "REQ-022_M4_20260919"
FILES = {
    ".gitignore",
    "main.py",
    "quality_knowledge/web/app.py",
    "quality_knowledge/web/major_case_pages.py",
    "quality_knowledge/web/templates/base.html",
    "quality_knowledge/web/templates/major_cases.html",
    "quality_knowledge/web/templates/major_case_detail.html",
    "quality_knowledge/web/templates/major_tasks.html",
    "quality_knowledge/web/templates/major_task_detail.html",
    "quality_knowledge/web/templates/major_repeat_detail.html",
    "quality_knowledge/web/templates/major_skills.html",
    "docs/requirements/REQ-022_MAJOR_CASE_KNOWLEDGE_DESIGN_V1.md",
    "docs/requirements/REQUIREMENT_INDEX.md",
    "docs/requirements/REQ-022_M4_DELIVERY.md",
    "tests/test_req022_major_cases.py",
    "scripts/build_req022_package.py",
}


def _files() -> list[Path]:
    names = set(FILES)
    names.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "quality_knowledge/major_cases").glob("*.py"))
    names.add("quality_knowledge/major_cases/schema.sql")
    paths = [ROOT / name for name in names]
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("PACKAGE_FILES_MISSING:" + ",".join(missing))
    forbidden = ("knowledge/", "output/", "data/", "sources/", "baseline_release/", "releases/")
    return sorted((path for path in paths if not path.relative_to(ROOT).as_posix().startswith(forbidden)), key=lambda p: p.relative_to(ROOT).as_posix())


def build(output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    files = _files()
    manifest = {
        "version": VERSION,
        "applicable_baseline": "V1.1 Quality Capability P0 RC1 / current cumulative source baseline",
        "contains_runtime_data": False,
        "files": [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
            for path in files
        ],
    }
    readme = """# REQ-022 M4 升级包

适用范围：当前 V1.1 Quality Capability P0 RC1 累计源码基线。

升级前备份现有业务 SQLite；本包不会搬迁或复制业务库。覆盖源码后继续使用
`start_quality_capability_p1.bat`，应用会在业务库同目录的 `major_knowledge` 下初始化独立
`knowledge.sqlite3`、附件目录及隔离 M8 运行目录。也可设置 `MAJOR_KNOWLEDGE_DATA_ROOT`
指向其他可写目录。

校验：核对 `REQ-022_MANIFEST.json` 中每个文件 SHA-256；启动后打开
`/knowledge/major-cases`。先使用合成 PDF/DOCX 验证上传、提取、人工确认和重复分析。

回滚：停止应用，恢复升级前源码或移除本包新增文件；不删除现有业务库。若只停用新能力，
移除导航入口即可。独立 `major_knowledge` 目录应先做一致性备份，再由管理员归档；回滚不要求
删除知识库或附件。真实模型、Windows BAT 和真实脱敏业务效果需在目标环境另行验收。
"""
    prefix = f"KNOWLEDGE_QUALITY_ENGINE_{VERSION}"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{prefix}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(f"{prefix}/REQ-022_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(f"{prefix}/README_REQ-022.md", readme)
    return {
        "path": str(output), "version": VERSION, "file_count": len(files),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "contains_runtime_data": False,
    }


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "baseline_release" / f"{VERSION}.zip"))
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
