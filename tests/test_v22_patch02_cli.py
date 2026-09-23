from pathlib import Path

import analyze


def test_force_stage_and_case_alias_are_supported():
    args = analyze.build_parser().parse_args([
        "--force", "--stage", "similarity", "--case", "QUERY-001", "--debug"
    ])
    assert args.force is True
    assert args.stage == "similarity"
    assert args.query_id == "QUERY-001"
    assert args.debug is True


def test_default_mode_is_incremental_all():
    args = analyze.build_parser().parse_args([])
    assert args.force is False
    assert args.overwrite is False
    assert args.stage == "all"


def test_run_bat_forwards_arguments():
    content = Path("run.bat").read_text(encoding="utf-8")
    assert "analyze.py %*" in content
