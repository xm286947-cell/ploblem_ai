"""#635: browser-level negative/recovery Mock tests using actual Major production JS.

The Chromium page executes the shipped product script. Only HTTP responses are
controlled fixtures. No backend mutation, remote credentials, or live Provider.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "quality_knowledge/web/templates"
SCRIPT = ROOT / "quality_knowledge/web/static/major_production.js"
CASE = "KCASE-UE-635"
EVENT = "EVT-UE-635"
BATCH = "MIMP-UE-635"


def _page(browser, *, case_id=None, batch_id=None, scenario="normal"):
    request = SimpleNamespace(
        url=SimpleNamespace(path="/p0/major-production"),
        query_params={},
        app=SimpleNamespace(state=SimpleNamespace(
            mature_quality_host=False, overall_shell_enabled=False
        )),
    )
    rendered = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True).get_template(
        "major_production.html"
    ).render(request=request, page_title="重大问题生产", api_prefix="/api/v2")
    # Avoid loading unrelated scripts/styles; this test runs only the real Major JS.
    import re
    rendered = re.sub(r'<script\b[^>]*src=[^>]*>\s*</script>', '', rendered)
    rendered = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', '', rendered)
    context = browser.new_context()
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.route("https://major-ued.mock/p0/major-production", lambda route: route.fulfill(
        status=200, content_type="text/html", body=rendered
    ))
    page.goto("https://major-ued.mock/p0/major-production", wait_until="domcontentloaded")
    if case_id or batch_id:
        page.evaluate("""([c,b]) => localStorage.setItem(
          'major-v11-context:/p0/major-production',
          JSON.stringify({caseId:c,eventId:c?'EVT-UE-635':null,batchId:b}))""",
          [case_id, batch_id])
    page.evaluate("""(scenario) => {
      const CASE='KCASE-UE-635', EVENT='EVT-UE-635', BATCH='MIMP-UE-635';
      const entries=['ISSUE_FACT','ROOT_CAUSE','ACTION','VERIFICATION'].map((t,i)=>({
        entry_id:'ENT-'+i,event_id:EVENT,entry_type:t,status:'PENDING',
        origin:'AI',content:'[受控 Mock] '+t
      }));
      const rows=Array.from({length:69},(_,i)=>({
        excel_row:i+2, itrs:['ITR-UE-'+i], title:'测试行'+(i+1),
        completeness:{importable:true},report_match:{match_status:'EXACT'}
      }));
      const M=window.__mock={calls:[],scenario,attempts:0,confirmFailures:0,
        confirmed:[],entriesVisible:false,published:false};
      window.P0_MAJOR_API='/api/v2';
      function reply(body,status=200,headers={}) {
        return {ok:status>=200&&status<300,status,
          headers:{get:(k)=>headers[k]||null},json:async()=>body};
      }
      window.fetch=async (url,options={}) => {
        const path=String(url);const method=options.method||'GET';
        M.calls.push({path,method});
        if(path.endsWith('/recent'))return reply({batches:[],cases:[]});
        if(path.endsWith('/excel/batches/'+BATCH))
          return reply({status:'PREVIEW',preview:{rows,importable:69,
            source_file:'synthetic.xls',mapping_version:'MOCK-635'}});
        if(path.endsWith('/excel/batches/'+BATCH+'/preflight'))
          return reply({confirmable:scenario!=='preflight_blocked',
            errors:scenario==='preflight_blocked'?[{row:7,error:'SOURCE_MAPPING_ERROR'}]:[]});
        if(path.endsWith('/excel/confirm')&&method==='POST')
          return reply({status:'COMPLETED',case_ids:[CASE]});
        if(path.endsWith('/cases/'+CASE))
          return reply({events:scenario==='multi_event'
             ?[{event_id:EVENT,standard_itr:'ITR-UE-1'},
               {event_id:'EVT-OTHER',standard_itr:'ITR-OTHER'}]
             :[{event_id:EVENT,standard_itr:'ITR-UE-1'}],
            entries:M.entriesVisible?entries.map(e=>M.confirmed.includes(e.entry_id)
              ?{...e,status:'CONFIRMED'}:e):[],source_links:[]});
        if(path.includes('/cases/'+CASE+'/analysis/diagnostics'))
          return reply({tasks:[{task_id:'TASK-FAIL-635',status:'FAILED',
            provider_calls:1,committed_objects:0,expected_objects:4,
            missing_types:['VERIFICATION'],failure_codes:[{code:'D01_OUTPUT_INCOMPLETE'}]}]});
        if(path.includes('/cases/'+CASE+'/analysis')&&method==='POST'){
          M.attempts++;
          if(scenario==='analysis_fail_retry'&&M.attempts===1)
            return reply({detail:'MAJOR_ANALYSIS_INCOMPLETE'},400,
              {'X-Major-Runtime-Task-ID':'TASK-FAIL-635'});
          M.entriesVisible=true;return reply({candidates:entries});
        }
        if(path.includes('/entries/')&&path.endsWith('/confirm')&&method==='POST'){
          const id=path.split('/entries/')[1].split('/confirm')[0];
          if(scenario==='analysis_fail_retry'&&id==='ENT-0'&&M.confirmFailures++===0)
            return reply({detail:'REVIEW_WRITE_CONFLICT'},409);
          M.confirmed.push(id);return reply({entry_type:entries.find(x=>x.entry_id===id).entry_type,
            revision_no:1});
        }
        if(path.includes('/events/')&&path.endsWith('/publish')&&method==='POST'){
          M.published=true;return reply({publication_status:'PUBLISHED',status:'ACTIVE',case_id:CASE});
        }
        return reply({detail:'UNEXPECTED_MOCK_ROUTE:'+path},404);
      };
    }""", scenario)
    page.add_script_tag(content=SCRIPT.read_text(encoding="utf-8"))
    return page, context, errors


def test_refresh_resumes_authoritative_saved_batch_without_post():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page,context,errors=_page(browser,case_id=CASE,batch_id=BATCH)
        page.wait_for_function("document.querySelector('[data-major-batch-table] tbody tr') !== null")
        assert page.locator('[data-major-recovery]').evaluate('(el)=>el.open')
        assert page.locator('[data-major-identity]').inner_text().find(CASE)>=0
        assert page.locator('[data-major-batch-table] tbody tr').count()==20
        assert page.locator('[data-major-recovered-confirm]').is_enabled()
        calls=page.evaluate("window.__mock.calls")
        assert not any(c['method']=='POST' for c in calls),calls
        assert not errors,errors
        context.close();browser.close()


def test_blocked_preflight_displays_row_reason_and_no_commit():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page,context,errors=_page(browser,batch_id=BATCH,scenario="preflight_blocked")
        page.wait_for_function("document.querySelector('[data-major-blocking-reasons]')?.textContent.includes('Excel')")
        assert "第 7 行" in page.locator('[data-major-blocking-reasons]').inner_text()
        page.locator('[data-major-recovered-confirm]').click()
        assert "不可确认" in page.locator('[data-major-excel-status]').inner_text() or (
          "不可确认" in page.locator('[data-major-message]').inner_text()
        )
        calls=page.evaluate("window.__mock.calls")
        assert not any('/excel/confirm' in c['path'] and c['method']=='POST' for c in calls),calls
        assert not errors,errors
        context.close();browser.close()


def test_incomplete_ai_and_review_failure_keep_publish_locked_then_retry():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page,context,errors=_page(browser,case_id=CASE,scenario="analysis_fail_retry")
        page.wait_for_function("document.querySelector('[data-major-identity]')?.textContent.includes('KCASE-UE-635')")
        assert page.locator('[data-major-publish]').is_disabled()
        page.locator('[data-major-analyze]').click()
        page.wait_for_function("document.querySelector('[data-major-analysis-diagnostics]')?.textContent.includes('D01_OUTPUT_INCOMPLETE')")
        assert page.locator('[data-major-publish]').is_disabled()
        assert page.locator('[data-major-analyze]').is_enabled()
        assert "未完成" in page.locator('[data-major-analysis-status]').inner_text() if page.locator('[data-major-analysis-status]').count() else "未完成" in page.locator('[data-major-workflow]').inner_text()
        page.locator('[data-major-analyze]').click()
        page.wait_for_function("document.querySelectorAll('[data-entry]').length===4")
        assert page.locator('[data-major-publish]').is_disabled()
        page.locator('[data-entry="ENT-0"]').click()
        page.wait_for_function("document.querySelector('[data-major-message]')?.textContent.includes('确认失败')")
        assert page.locator('[data-entry="ENT-0"]').is_enabled()
        assert page.locator('[data-major-publish]').is_disabled()
        for index in range(4):
            page.locator(f'[data-entry="ENT-{index}"]').click()
        page.wait_for_function("document.querySelector('[data-major-publish]').disabled===false")
        assert page.evaluate('window.__mock.confirmed.length')==4
        page.locator('[data-major-publish]').click()
        page.wait_for_function("window.__mock.published === true")
        assert not errors,errors
        context.close();browser.close()


def test_multi_event_requires_explicit_event_selection_before_analysis():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page,context,errors=_page(browser,case_id=CASE,scenario="multi_event")
        page.wait_for_function("document.querySelector('[data-major-state]')?.dataset.stageCode==='EVENT_SELECTION_REQUIRED'")
        assert page.locator('[data-major-analyze]').is_enabled()
        page.locator('[data-major-analyze]').click()
        assert page.evaluate('window.__mock.attempts')==0
        assert page.locator('[data-major-publish]').is_disabled()
        assert not errors,errors
        context.close();browser.close()
