import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const sourcePath = fileURLToPath(
  new URL('../../quality_knowledge/web/static/hardware_case_knowledge_production.js', import.meta.url)
);
const source = fs.readFileSync(sourcePath, 'utf8');

test('human review UI submits durable review decisions', () => {
  assert.match(source, /\/human-review/);
  assert.match(source, /confirmed_content:\s*decision === 'CONFIRM'/);
  assert.match(source, /data-human-review-action/);
  assert.match(source, /人工修正确认已保存，可继续正式知识发布/);
});

test('promotion stays blocked while durable review is REQUIRED', () => {
  assert.match(
    source,
    /candidate_asset\?\.production_review_status !== 'REQUIRED'/
  );
});

test('human review preserves Evidence as read-only comparison context', () => {
  assert.match(source, /candidate\.evidence/);
  assert.match(source, /humanReviewProtectedTop/);
  assert.match(source, /'source_fact'/);
  assert.match(source, /'evidence'/);
});


test('human review editor exposes value only and keeps metadata read-only', () => {
  assert.match(source, /VALUE ONLY/);
  assert.match(source, /semanticKeys/);
  assert.match(source, /\['name', 'value', 'unit'\]/);
  assert.match(source, /\[\.\.\.path, key\]/);
  assert.match(source, /evidence_block_ids/);
  assert.match(source, /Evidence metadata is read-only/);
});


test('formal review submits approval only and never resends candidate content', () => {
  const start = source.indexOf("if (action === 'review')");
  const end = source.indexOf("} else if (action === 'publish')", start);
  assert.ok(start >= 0 && end > start);
  const formalReviewBlock = source.slice(start, end);
  assert.match(formalReviewBlock, /reviewer/);
  assert.match(formalReviewBlock, /review_comment/);
  assert.doesNotMatch(formalReviewBlock, /confirmed_content/);
});

test('human review supports evidence-bound parameter add and remove', () => {
  assert.match(source, /data-human-review-add-parameter/);
  assert.match(source, /data-human-review-remove-parameter/);
  assert.match(source, /data-human-review-new-evidence/);
  assert.match(source, /__review_original_index/);
  assert.match(source, /__review_new/);
});


test('running batch progress auto-refreshes without manual page refresh', () => {
  assert.match(source, /function ensurePassiveBatchPolling\(batch\)/);
  assert.match(source, /batch\.status === 'RUNNING'/);
  assert.match(source, /refreshBatchSnapshot\(batchId\)/);
  assert.match(source, /window\.setInterval\(tick, 1000\)/);
  assert.match(source, /stopPassiveBatchPolling\(\)/);
});


const productionTemplatePath = fileURLToPath(
  new URL('../../quality_knowledge/web/templates/hardware_case_knowledge_production.html', import.meta.url)
);
const productionTemplate = fs.readFileSync(productionTemplatePath, 'utf8');
const consumptionTemplatePath = fileURLToPath(
  new URL('../../quality_knowledge/web/templates/hardware_case_knowledge_consumption.html', import.meta.url)
);
const consumptionTemplate = fs.readFileSync(consumptionTemplatePath, 'utf8');
const consumptionSourcePath = fileURLToPath(
  new URL('../../quality_knowledge/web/static/hardware_case_knowledge_consumption.js', import.meta.url)
);
const consumptionSource = fs.readFileSync(consumptionSourcePath, 'utf8');

test('knowledge production workflow is visible and business-semantic in Chinese', () => {
  assert.match(productionTemplate, /Word 导入 → AI 分析 → 人工确认 → 发布正式知识 → 生成检索数据 → 搜索使用/);
  assert.match(productionTemplate, /本次导入任务/);
  assert.match(productionTemplate, /问题事实提取/);
  assert.match(productionTemplate, /工程知识生成/);
  assert.match(productionTemplate, /证据校验/);
  assert.match(productionTemplate, /正式知识发布/);
  assert.match(productionTemplate, /1\. 发布前检查/);
  assert.match(productionTemplate, /2\. 提交正式审核/);
  assert.match(productionTemplate, /3\. 确认审核通过/);
  assert.match(productionTemplate, /4\. 发布正式知识/);
  assert.match(productionTemplate, /5\. 验证发布结果/);
  assert.match(productionTemplate, /6\. 生成检索数据/);
  assert.match(source, /function renderNextStep/);
  assert.match(source, /AI 分析/);
  assert.match(source, /搜索使用/);
});

test('publish verification and search-data generation are separate explicit actions', () => {
  const verifyStart = source.indexOf('async function verifyPublication');
  const projectStart = source.indexOf('async function projectConsumption');
  const openItemStart = source.indexOf('async function openItem', projectStart);
  assert.ok(verifyStart >= 0 && projectStart > verifyStart && openItemStart > projectStart);
  const verifyBlock = source.slice(verifyStart, projectStart);
  const projectBlock = source.slice(projectStart, openItemStart);
  assert.match(verifyBlock, /promotion\/verify/);
  assert.doesNotMatch(verifyBlock, /consumption\/project/);
  assert.match(projectBlock, /consumption\/project/);
  assert.match(projectBlock, /检索数据已生成/);
});

test('knowledge search unavailable state gives a clear Chinese recovery route', () => {
  assert.match(consumptionTemplate, /正式知识检索数据尚未生成/);
  assert.match(consumptionTemplate, /返回知识生产工作台/);
  assert.match(consumptionTemplate, /\/p0\/hardware-cases\/knowledge-production/);
  assert.match(consumptionSource, /执行“生成检索数据”/);
  assert.doesNotMatch(consumptionTemplate, /请先重建 Consumption Projection/);
});


test('ambiguous publish exposes explicit reconciliation and never republishes', () => {
  assert.match(productionTemplate, /需要处理发布对账/);
  assert.match(productionTemplate, /data-promotion-reconcile/);
  assert.match(productionTemplate, /处理发布对账/);
  assert.match(source, /reconciliation_required/);
  assert.match(source, /reconciliation_operation_type === 'PUBLISH'/);
  const start = source.indexOf('async function reconcilePublication');
  const end = source.indexOf('async function verifyPublication', start);
  assert.ok(start >= 0 && end > start);
  const reconcileBlock = source.slice(start, end);
  assert.match(reconcileBlock, /promotion\/reconcile/);
  assert.match(reconcileBlock, /不会再次发起“发布正式知识”/);
  assert.doesNotMatch(reconcileBlock, /promotion\/publish/);
});

test('pending PUBLISH API state reveals reconciliation and disables unsafe actions', async () => {
  class FakeElement {
    constructor() {
      this.dataset = {};
      this.textContent = '';
      this.innerHTML = '';
      this.hidden = false;
      this.disabled = false;
      this.value = '';
      this.listeners = {};
      this.classList = {toggle() {}, add() {}, remove() {}};
      this.strongs = [];
    }

    addEventListener(type, handler) {
      this.listeners[type] = handler;
    }

    querySelectorAll(selector) {
      return selector === 'strong' ? this.strongs : [];
    }

    scrollIntoView() {}
  }

  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement());
    return elements.get(selector);
  };
  element('[data-batch-summary]').strongs = Array.from(
    {length: 6},
    () => new FakeElement()
  );
  const item = {
    item_id: 'I1',
    batch_id: 'B1',
    business_case_id: 'A0152',
    source_file: 'A0152-demo.docx',
    parse: 'PASS',
    stage_a: 'PASS',
    stage_b: 'PASS',
    gate: 'PASS',
    result: 'CANDIDATE_READY',
    orchestration_status: 'CANDIDATE_READY',
    candidate_id: 'C1',
    candidate: null,
  };
  const promotion = {
    status: 'REVIEW_CONFIRMED',
    reconciliation_required: true,
    reconciliation_operation_type: 'PUBLISH',
    reconciliation_error_code: 'PUBLISH_RECONCILIATION_REQUIRED',
  };
  const requests = [];
  const response = (payload) => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    async json() { return payload; },
  });
  const fetch = async (url, options = {}) => {
    const method = String(options.method || 'GET').toUpperCase();
    requests.push({url, method});
    if (url.endsWith('/batches?limit=50')) {
      return response({items: [{batch_id: 'B1', status: 'READY_FOR_REVIEW'}]});
    }
    if (url.endsWith('/batches/B1')) {
      return response({
        batch_id: 'B1',
        status: 'READY_FOR_REVIEW',
        summary: {TOTAL: 1, QUEUED: 0, RUNNING: 0, CANDIDATE_READY: 1, REVIEW: 0, FAILED: 0},
        items: [item],
      });
    }
    if (url.endsWith('/items/I1') && method === 'GET') return response(item);
    if (url.endsWith('/items/I1/promotion') && method === 'GET') {
      return response(promotion);
    }
    if (url.endsWith('/items/I1/promotion/reconcile') && method === 'POST') {
      return response({status: 'PUBLISHED_PENDING_QUERY_BACK', reconciled: true});
    }
    throw new Error('Unexpected request: ' + method + ' ' + url);
  };
  const root = new FakeElement();
  root.dataset.api = '/api/v2/hardware-cases/r1/workbench';
  root.querySelector = element;
  root.querySelectorAll = () => [];
  const windowObject = {
    location: {href: 'http://localhost/p0/hardware-cases/knowledge-production?batch=B1&item=I1'},
    history: {replaceState() {}},
    scrollY: 0,
    confirm: () => true,
    setInterval() { return 1; },
    clearInterval() {},
  };
  const context = vm.createContext({
    window: windowObject,
    document: {
      querySelector: (selector) => selector === '[data-r1-workbench]' ? root : null,
    },
    fetch,
    URL,
    URLSearchParams,
    encodeURIComponent,
    FormData: class {},
    console,
    setTimeout,
    clearTimeout,
  });
  vm.runInContext(source, context, {filename: sourcePath});
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(element('[data-promotion-reconciliation]').hidden, false);
  assert.match(
    element('[data-promotion-status]').textContent,
    /PUBLISH_RECONCILIATION_REQUIRED/
  );
  assert.match(
    element('[data-promotion-reconciliation-detail]').textContent,
    /不会再次发起发布/
  );
  assert.equal(element('[data-promotion-reconcile]').disabled, false);
  assert.equal(element('[data-promotion-publish]').disabled, true);
  assert.equal(element('[data-promotion-verify]').disabled, true);
  assert.equal(element('[data-project-consumption]').disabled, true);

  await element('[data-promotion-reconcile]').listeners.click();
  assert.equal(
    requests.filter(({url, method}) => method === 'POST' && url.endsWith('/promotion/reconcile')).length,
    1
  );
  assert.equal(
    requests.filter(({url}) => url.endsWith('/promotion/publish')).length,
    0
  );
});
