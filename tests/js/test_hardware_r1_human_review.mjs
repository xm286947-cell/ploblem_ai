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
  assert.match(source, /人工修正已写入 Durable Candidate/);
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
