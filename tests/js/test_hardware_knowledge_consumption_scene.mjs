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
    this.attributes = {};
    this.classList = {toggle() {}, add() {}, remove() {}};
  }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

const response = payload => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  async json() { return payload; },
});

test('Wave3B scene tabs drive formal scene query and explicit device aggregation', async () => {
  const elements = new Map();
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement(selector));
    return elements.get(selector);
  };

  const scenarios = ['research', 'risk', 'market'].map(name => {
    const button = new FakeElement('scenario-' + name);
    button.dataset.scenario = name;
    return button;
  });
  const filters = ['interface', 'signal', 'device'].map(name => {
    const input = element('[data-knowledge-filter="' + name + '"]');
    input.dataset.knowledgeFilter = name;
    return input;
  });

  const root = new FakeElement('root');
  root.dataset.api = '/api/public/hardware-knowledge/v1';
  root.querySelector = element;
  root.querySelectorAll = selector => {
    if (selector === '[data-scenario]') return scenarios;
    if (selector === '[data-knowledge-filter]') return filters;
    return [];
  };

  const requested = [];
  const formalDeviceResult = {
    contract_version: 'hardware-knowledge-consumption/v1',
    projection_schema_version: 1,
    knowledge_id: 'K1',
    public_ref: 'P1',
    business_case_id: 'CASE-1',
    title: 'UART drive risk',
    symptom: 'garbled output',
    occurrence_condition: 'under load',
    failure_mode: 'drive margin',
    root_cause: 'insufficient drive',
    failure_mechanism: 'voltage droop',
    analysis_process: 'waveform comparison',
    actions: 'review output topology',
    verification_result: 'stable',
    engineering_rule: 'check drive margin',
    design_constraint: 'match external load',
    diagnostic_clue: 'droop only under load',
    verification_method: 'measure loaded waveform',
    applicability: 'digital output',
    conclusion: 'drive margin matters',
    interface: 'UART',
    signal: 'TX',
    key_parameters: [],
    device_refs: [{
      category: 'MCU',
      generic_name_or_series: 'MCU',
      internal_material_no: null,
      manufacturer: null,
      manufacturer_part_no: null,
      evidence_refs: [],
      status: 'EXPLICIT',
    }],
    evidence_refs: ['EV-1'],
    source_domain: 'HARDWARE_CASE',
    source_object_type: 'HARDWARE_CASE',
    formal_revision: 1,
    formal_status: 'ACTIVE',
    formal_object_hash: 'a'.repeat(64),
    projected_at: '2026-10-06T00:00:00Z',
    match_score: 150,
    match_reasons: [],
  };

  const fetch = async url => {
    requested.push(String(url));
    const isDeviceRisk = String(url).includes('scene=DEVICE_RISK');
    return response({
      contract_version: 'hardware-knowledge-consumption/v1',
      results: isDeviceRisk ? [formalDeviceResult] : [],
    });
  };

  const context = vm.createContext({
    document: {
      querySelector: selector =>
        selector === '[data-hc-page="knowledge-consumption"]' ? root : null,
    },
    fetch,
    URLSearchParams,
    encodeURIComponent,
    console,
    setTimeout,
    clearTimeout,
    Map,
    Set,
  });

  const scriptPath = fileURLToPath(new URL(
    '../../quality_knowledge/web/static/hardware_case_knowledge_consumption.js',
    import.meta.url
  ));
  vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, {filename: scriptPath});

  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(requested[0].includes('scene=RND_DIAGNOSIS'));

  scenarios[1].listeners.click();
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));

  assert.ok(requested.some(url => url.includes('scene=DEVICE_RISK')));
  const panel = element('[data-device-risk-summary]');
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /器件风险聚合/);
  assert.match(panel.innerHTML, /MCU/);
  assert.match(panel.innerHTML, /1 个 Case/);
  assert.match(panel.innerHTML, /voltage droop/);
  assert.match(panel.innerHTML, /match external load/);
});
