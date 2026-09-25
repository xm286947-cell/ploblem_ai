from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")


def test_ui_next_01_missing_pdf_is_visible_and_post_not_run():
    assert "function importPreconditionError()" in HTML
    assert "if(!file)return '请先选择 PDF 规格书'" in HTML
    assert "if(error){$('#importState').textContent=error;return msg(error,true)}" in HTML


def test_ui_next_02_unconfirmed_identity_is_visible_and_post_not_run():
    assert "if(!IDENTITY_CONFIRMED)return '请先完成基础信息识别并人工确认'" in HTML


def test_ui_next_03_confirmed_pdf_posts_jobs_exactly_once_from_explicit_action():
    start = HTML.index("async function startParameterRecognition()")
    end = HTML.index("async function pollJob", start)
    block = HTML[start:end]
    assert block.count("api('/api/documents/jobs'") == 1
    assert "id=\"importSubmit\" type=\"button\"" in HTML
    assert "$('#importSubmit').addEventListener('click',startParameterRecognition)" in HTML


def test_ui_next_04_success_surfaces_job_id_and_enters_existing_poll_flow():
    assert "if(!r.job_id)throw Error('任务创建失败：服务未返回 job_id')" in HTML
    assert "$('#importState').textContent='任务已创建：'+r.job_id;pollJob(r.job_id)" in HTML
    assert "if(r.result?.device_id)await openReview(r.result.device_id)" in HTML


def test_ui_next_05_post_failure_is_visible_and_button_recovers():
    assert "$('#importState').textContent='任务创建失败：'+x.message" in HTML
    assert "IMPORT_JOB_CREATING=false;button.disabled=false;button.textContent='进入参数识别与覆盖度分析'" in HTML


def test_ui_next_06_double_click_cannot_create_duplicate_job():
    assert "if(IMPORT_JOB_CREATING)return" in HTML
    assert "IMPORT_JOB_CREATING=true;button.disabled=true" in HTML
