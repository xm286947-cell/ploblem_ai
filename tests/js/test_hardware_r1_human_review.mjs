import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
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
