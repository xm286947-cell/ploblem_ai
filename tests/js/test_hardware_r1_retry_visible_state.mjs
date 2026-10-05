import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

class FakeElement {
  constructor(name = '') {
    this.name = name;
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

const response = (payload, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status >= 200 && status < 300 ? 'OK' : 'ERROR',
  async json() { return payload; },
});

function item(overrides = {}) {
  return {
    item_id: 'I1',
    batch_id: 'B1',
    business_case_id: 'A0152',
    source_file: 'A0152-demo.docx',
    parse: 'PASS',
    stage_a: 'PASS',
    stage_b: 'FAILED',
    gate: 'WAITING',
    result: 'FAILED',
    orchestration_status: 'FAILED',
    failed_stage: 'STAGE_B',
    error_code: 'PROVIDER_TIMEOUT',
    provider_calls: 1,
    duration_ms: 123,
    run_ref: 'run-1',
    updated_at: '2026-10-06T00:00:00Z',
    candidate: null,
    candidate_id: null,
    evidence_validation: null,
    ...overrides,
  };
}

test('retry shows visible Stage B running state before delayed POST completes', async () => {
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement(selector));
    return elements.get(selector);
  };
  element('[data-batch-summary]').strongs = Array.from({length: 6}, () => new FakeElement('strong'));
  element('[data-result-filter]').value = '';

  const root = new FakeElement('root');
  root.dataset.api = '/api/v2/hardware-cases/r1/workbench';
  root.querySelector = element;

  const intervalCallbacks = [];
  let retryStarted = false;
  let finalized = false;
  let postCount = 0;
  let resolvePost;

  const failed = item();
  const running = item({
    stage_b: 'RUNNING',
    result: 'RUNNING',
    orchestration_status: 'RUNNING',
    error_code: null,
    provider_calls: 2,
    duration_ms: 456,
    updated_at: '2026-10-06T00:00:01Z',
  });
  const done = item({
    stage_b: 'PASS',
    gate: 'PASS',
    result: 'CANDIDATE_READY',
    orchestration_status: 'CANDIDATE_READY',
    failed_stage: null,
    error_code: null,
    provider_calls: 2,
    duration_ms: 789,
    updated_at: '2026-10-06T00:00:02Z',
  });

  const batchFor = (current) => ({
    batch_id: 'B1',
    status: current.result === 'FAILED' ? 'PARTIAL_FAILURE' : current.result === 'RUNNING' ? 'RUNNING' : 'READY_FOR_REVIEW',
    summary: {
      TOTAL: 1,
      QUEUED: 0,
      RUNNING: current.result === 'RUNNING' ? 1 : 0,
      CANDIDATE_READY: current.result === 'CANDIDATE_READY' ? 1 : 0,
      REVIEW: 0,
      FAILED: current.result === 'FAILED' ? 1 : 0,
    },
    items: [current],
  });

  const fetch = async (url, options = {}) => {
    const method = String(options.method || 'GET').toUpperCase();
    if (url.endsWith('/batches?limit=50')) {
      return response({items: [{batch_id: 'B1', status: 'PARTIAL_FAILURE'}]});
    }
    if (url.endsWith('/batches/B1')) {
      return response(batchFor(finalized ? done : retryStarted ? running : failed));
    }
    if (url.endsWith('/items/I1') && method === 'GET') {
      return response(finalized ? done : retryStarted ? running : failed);
    }
    if (url.endsWith('/items/I1/retry-failed-stage') && method === 'POST') {
      postCount += 1;
      retryStarted = true;
      return new Promise((resolve) => {
        resolvePost = () => {
          finalized = true;
          resolve(response(done));
        };
      });
    }
    if (url.endsWith('/items/I1/promotion')) {
      return response({status: 'NOT_STARTED'});
    }
    throw new Error('Unexpected request: ' + method + ' ' + url);
  };

  const windowObject = {
    location: {href: 'http://localhost/p0/hardware-cases/production?batch=B1&item=I1'},
    history: {replaceState() {}},
    scrollY: 0,
    confirm: () => true,
    setInterval(fn) {
      intervalCallbacks.push(fn);
      return intervalCallbacks.length;
    },
    clearInterval() {},
  };

  const context = vm.createContext({
    window: windowObject,
    document: {querySelector: (selector) => selector === '[data-r1-workbench]' ? root : null},
    fetch,
    URL,
    URLSearchParams,
    encodeURIComponent,
    FormData: class {},
    console,
    setTimeout,
    clearTimeout,
  });

  const scriptPath = fileURLToPath(new URL('../../quality_knowledge/web/static/hardware_case_knowledge_production.js', import.meta.url));
  vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {filename: scriptPath});

  // Allow initial History -> Batch -> Item loading to finish.
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));

  const retryButton = element('[data-item-retry]');
  const runButton = element('[data-item-run]');
  const forceButton = element('[data-force-item]');
  const inlineStatus = element('[data-item-action-status]');

  assert.equal(retryButton.disabled, false);
  const actionPromise = retryButton.listeners.click();

  // Must be visible before the delayed POST resolves.
  assert.equal(postCount, 1);
  assert.equal(inlineStatus.hidden, false);
  assert.equal(inlineStatus.textContent, '正在重试 Stage B…');
  assert.equal(retryButton.textContent, 'Retrying Stage B…');
  assert.equal(retryButton.disabled, true);
  assert.equal(runButton.disabled, true);
  assert.equal(forceButton.disabled, true);

  // A second click while pending must not submit another retry.
  retryButton.listeners.click();
  assert.equal(postCount, 1);

  // Drive the current-item polling callback while the POST is still pending.
  assert.ok(intervalCallbacks.length > 0);
  await intervalCallbacks.at(-1)();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(element('[data-detail-status]').innerHTML, /RUNNING/);
  assert.match(element('[data-detail-stages]').innerHTML, /Stage B/);
  assert.match(element('[data-detail-stages]').innerHTML, /RUNNING/);
  assert.equal(element('[data-detail-provider-calls]').textContent, 2);
  assert.equal(element('[data-detail-duration]').textContent, '456 ms');

  resolvePost();
  await actionPromise;
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(postCount, 1);
  assert.equal(inlineStatus.textContent, '重试 Stage B 完成：CANDIDATE_READY');
  assert.equal(runButton.disabled, false);
  assert.equal(forceButton.disabled, false);
  assert.equal(retryButton.disabled, true);
  assert.equal(retryButton.textContent, 'Retry Failed Stage');
  assert.match(element('[data-detail-status]').innerHTML, /CANDIDATE_READY/);
});
