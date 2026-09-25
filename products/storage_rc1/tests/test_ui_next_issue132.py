from pathlib import Path
import re

HTML = Path(__file__).parents[1] / "storage_life" / "index.html"
TEXT = HTML.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    marker = f"function {name}("
    start = TEXT.index(marker)
    end = TEXT.find("\nfunction ", start + len(marker))
    if end < 0:
        end = TEXT.find("\nasync function ", start + len(marker))
    return TEXT[start:] if end < 0 else TEXT[start:end]


def test_ui_next_01_no_pdf_is_explicit_and_post_not_run():
    body = _function_body("importPreconditionError")
    assert "请先选择 PDF 规格书" in body
    assert "/api/documents/jobs" not in body


def test_ui_next_02_unconfirmed_identity_is_explicit_and_post_not_run():
    body = _function_body("importPreconditionError")
    assert "请先完成基础信息识别并人工确认" in body
    assert "/api/documents/jobs" not in body


def test_ui_next_03_valid_action_has_exactly_one_job_post():
    body = _function_body("startParameterRecognition")
    assert body.count("api('/api/documents/jobs'") == 1
    assert "method:'POST'" in body
    assert 'id="importSubmit" type="button"' in TEXT
    assert '<form id="importForm" novalidate>' in TEXT


def test_ui_next_04_success_exposes_job_id_and_enters_existing_poll_flow():
    body = _function_body("startParameterRecognition")
    assert "r.job_id" in body
    assert "任务已创建：" in body
    assert "pollJob(r.job_id)" in body
    assert "openReview(r.result.device_id)" in TEXT


def test_ui_next_05_post_failure_is_visible_and_button_recovers():
    body = _function_body("startParameterRecognition")
    assert "任务创建失败：" in body
    assert "msg(x.message,true)" in body
    assert "IMPORT_JOB_CREATING=false" in body
    assert "button.disabled=false" in body


def test_ui_next_06_double_click_cannot_create_duplicate_job():
    body = _function_body("startParameterRecognition")
    assert "if(IMPORT_JOB_CREATING)return" in body
    assert body.index("IMPORT_JOB_CREATING=true") < body.index("api('/api/documents/jobs'")
    assert "$('#importSubmit').addEventListener('click',startParameterRecognition)" in TEXT


def test_p08_import_coverage_regression_contract():
    assert "async function identifyDocument()" in TEXT
    assert "function confirmIdentity()" in TEXT
    assert "async function pollJob(id)" in TEXT
    assert "recognitionCoverage" in TEXT
    assert "参数确认工作台" in TEXT
