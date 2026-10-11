"""#635 P0 fail-closed browser Mock: source form, four-type review and publish retry."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "quality_knowledge" / "web" / "templates"
SOURCE = ROOT / "quality_knowledge" / "web" / "static" / "major_production.js"

MOCK_ROUTER = r"""
(count) => {
 const M = {calls: [], confirmed: [], entryCount: count,
            publishCalls: 0, caseReads: 0, publishError: false};
 window.__majorGate = M;
 window.P0_MAJOR_API = '/api/v2';
 const TYPES = ['ISSUE_FACT', 'ROOT_CAUSE', 'ACTION', 'VERIFICATION'];
 const entries = () => TYPES.slice(0, M.entryCount).map((type,i) => ({
   entry_id: 'ENT-' + i, event_id: 'EVENT-SYN',
   entry_type: type, status: M.confirmed.includes('ENT-'+i) ? 'CONFIRMED' : 'PENDING',
   origin: 'AI', content: '[Mock] '+type
 }));
 const reply = (body,status=200) => ({
   ok: status>=200 && status<300, status,
   headers: {get: () => null}, json: async () => body
 });
 window.fetch = async (url,options={}) => {
   const path=String(url),method=options.method || 'GET';
   M.calls.push({path,method});
   if (path.endsWith('/recent')) return reply({batches: [], cases: []});
   if (path.endsWith('/sources') && method==='POST') return reply({
     case:{case_id:'CASE-SYN'},event:{event_id:'EVENT-SYN',standard_itr:'ITR-SYN'},
     document:{original_filename:'synthetic.pdf',version_no:1}
   });
   if (path.endsWith('/cases/CASE-SYN')) {
     M.caseReads++;
     return reply({events:[{event_id:'EVENT-SYN',standard_itr:'ITR-SYN'}],
                   entries:entries(),source_links:[]});
   }
   if (path.includes('/cases/CASE-SYN/analysis') && method==='POST')
     return reply({candidates:entries()});
   if (path.includes('/entries/') && path.endsWith('/confirm') && method==='POST') {
     const id=path.split('/entries/')[1].split('/confirm')[0];
     M.confirmed.push(id);
     const found=entries().find(x=>x.entry_id===id);
     return reply({entry_type:found ? found.entry_type : 'ISSUE_FACT',revision_no:1});
   }
   if (path.includes('/events/EVENT-SYN/publish') && method==='POST') {
     M.publishCalls++;
     return M.publishError ? reply({detail:'GATEWAY_TIMEOUT'},502) :
       reply({publication_status:'PUBLISHED',status:'ACTIVE',case_id:'CASE-SYN'});
   }
   if (path.includes('/analysis/diagnostics')) return reply({tasks:[]});
   return reply({detail:'UNEXPECTED_MOCK_ROUTE: '+path},404);
 };
}
"""


def rendered_html():
    request = SimpleNamespace(
        url=SimpleNamespace(path="/p0/major-production"),
        query_params={},
        app=SimpleNamespace(state=SimpleNamespace(
            mature_quality_host=False, overall_shell_enabled=False,
        )),
    )
    env=Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),autoescape=True)
    html=env.get_template('major_production.html').render(
        request=request,page_title="重大问题生产",api_prefix="/api/v2")
    soup=BeautifulSoup(html,'html.parser')
    for tag in soup.select('script[src], link[href]'):
        tag.decompose()
    return str(soup)


def check_case(browser, expected_count:int):
    page=browser.new_page(viewport={"width":1200,"height":940})
    page.set_default_timeout(10000)
    errors=[]
    page.on("pageerror",lambda err: errors.append(str(err)))
    page.set_content(rendered_html(),wait_until="load")
    page.evaluate(MOCK_ROUTER,expected_count)
    page.add_script_tag(content=SOURCE.read_text(encoding="utf-8"))
    page.locator('[data-major-single-source] summary').click()
    intake=page.locator('form[data-major-intake]')
    intake.locator('input[name=title]').fill('Synthetic Case')
    intake.locator('input[name=standard_itr]').fill('ITR-SYN')
    intake.locator('input[name=file]').set_input_files({
        'name':'synthetic.pdf','mimeType':'application/pdf',
        'buffer': b'%PDF-1.4\n%%EOF'})
    intake.locator('button[type=submit]').click()
    page.wait_for_function(
        "document.querySelector('[data-major-workflow]')?.hidden===false")
    assert '导入成功' in page.locator('[data-major-source-status]').inner_text()
    assert 'Cannot read properties of null' not in page.locator('[data-major-message]').inner_text()
    page.locator('[data-major-analyze]').click()
    page.wait_for_function(
        'n => document.querySelectorAll("[data-entry]").length===n',
        arg=expected_count)
    for _ in range(expected_count):
        page.locator('[data-entry]:not([disabled])').first.click()
        page.wait_for_function(
          'n => window.__majorGate.confirmed.length===n',
          arg=_+1)
        page.wait_for_timeout(50)
    calls=page.evaluate('window.__majorGate.caseReads')
    assert calls >= expected_count + 1, 'each confirmation must re-check server Case'
    if expected_count < 4:
        assert page.locator('[data-major-publish]').is_disabled(), (
            'P0: incomplete required four types must not unlock Publish')
        assert page.evaluate('window.__majorGate.publishCalls') == 0
    else:
        assert not page.locator('[data-major-publish]').is_disabled(), (
            'all four server-confirmed types should permit Publish')
        page.evaluate('window.__majorGate.publishError=true')
        page.locator('[data-major-publish]').click()
        page.wait_for_function(
            "document.querySelector('[data-major-publish]')?.textContent.includes('待核实')")
        assert page.locator('[data-major-publish]').is_disabled(), (
            'ambiguous publish HTTP response must fail closed')
        assert page.evaluate('window.__majorGate.publishCalls') == 1
        assert '避免重复发布' in page.locator('[data-major-message]').inner_text()
    assert not errors, errors
    print(f'P0_BROWSER_NEGATIVE_GATE_{expected_count}_TYPES=PASS')
    page.close()


def main():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            check_case(browser,2)
            check_case(browser,4)
        finally:
            browser.close()
    print('MAJOR_P0_FAIL_CLOSED_CHROMIUM_MOCK=PASS')


if __name__=="__main__":
    main()
