import json
from concurrent.futures import ThreadPoolExecutor

from quality_knowledge.reverse_quality_store import ReverseQualityResult, SQLiteReverseQualityRepository


def _complete(repo, run, value='掉电后计数保持'):
    repo.complete_run(
        run['run_id'],
        fields={'customer_task': {
            'value': value,
            'source_type': 'FACT',
            'evidence_ids': ['cs.description'],
            'confidence': 1.0,
            'review_status': 'PENDING',
            'reviewer_edit': '',
        }},
        evidence={'cs.description': {'id': 'cs.description', 'value': value}},
        scene_match={
            'status': 'NEED_REVIEW',
            'matched_scene_id': '',
            'match_reason': '',
            'missing_condition': '次数待确认',
        },
        model='test-model',
        input_payload={'canonical_itr': 'ITR-001', 'evidence': {'cs.description': {'value': value}}},
    )


def test_reverse_quality_store_pragmas_schema_and_result_contract(tmp_path):
    db_path = tmp_path / 'reverse_quality_v01.db'
    repo = SQLiteReverseQualityRepository(db_path)
    assert db_path.exists()
    with repo.connect() as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
        assert connection.execute('PRAGMA busy_timeout').fetchone()[0] >= 10000
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            'reverse_quality_analysis', 'reverse_quality_run', 'reverse_quality_field_result',
            'reverse_quality_evidence', 'reverse_quality_missing_information',
            'reverse_quality_human_review', 'reverse_quality_scene_match',
        } <= tables
    dto = ReverseQualityResult(
        result_version='reverse-quality-v0.1', analysis_id='A1', run_id='R1', status='PENDING_REVIEW',
        identity={'canonical_itr': 'ITR-001', 'product_code': 'PLC', 'taxonomy_version_id': 'T1', 'source_hash': 'h1'},
        fields={}, missing_information=[], scene_match={'status': 'NEED_REVIEW'}, model='m',
    )
    assert json.loads(dto.to_json()) == dto.to_dict()
    assert dto.to_json() == dto.to_json()


def test_failed_run_never_replaces_latest_valid_result(tmp_path):
    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    run1 = repo.start_run(canonical_itr='ITR-001', product_code='PLC', taxonomy_version_id='T1',
                          source_hash='hash-1', input_payload={})
    _complete(repo, run1, '有效结果')
    run2 = repo.start_run(canonical_itr='ITR-001', product_code='PLC', taxonomy_version_id='T1',
                          source_hash='hash-2', input_payload={})
    repo.fail_run(run2['run_id'], 'simulated failure')
    latest = repo.get_latest('ITR-001')
    assert latest['run_id'] == run1['run_id']
    assert latest['review']['customer_task']['value'] == '有效结果'
    runs = repo.list_runs('ITR-001')
    assert [row['status'] for row in runs] == ['FAILED', 'COMPLETED']


def test_duplicate_execution_keeps_history_and_uses_new_run_id(tmp_path):
    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    run1 = repo.start_run(canonical_itr='ITR-001', product_code='PLC', taxonomy_version_id='T1',
                          source_hash='same-hash', input_payload={})
    _complete(repo, run1)
    run2 = repo.start_run(canonical_itr='ITR-001', product_code='PLC', taxonomy_version_id='T1',
                          source_hash='same-hash', input_payload={})
    repo.reuse_run(run2['run_id'], run1['run_id'])
    latest = repo.get_latest('ITR-001')
    assert latest['run_id'] == run2['run_id']
    assert latest['run_id'] != run1['run_id']
    assert latest['review']['customer_task']['value'] == '掉电后计数保持'
    runs = repo.list_runs('ITR-001')
    assert len(runs) == 2
    assert runs[0]['reused_from_run_id'] == run1['run_id']


def test_concurrent_starts_get_unique_monotonic_run_sequences(tmp_path):
    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    def start(index):
        return repo.start_run(canonical_itr='ITR-CONCURRENT', product_code='PLC', taxonomy_version_id='T1',
                              source_hash=f'hash-{index}', input_payload={})
    with ThreadPoolExecutor(max_workers=8) as executor:
        runs = list(executor.map(start, range(8)))
    assert len({row['run_id'] for row in runs}) == 8
    assert sorted(row['run_seq'] for row in runs) == list(range(1, 9))


def test_human_review_and_scene_match_are_append_only(tmp_path):
    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    run = repo.start_run(canonical_itr='ITR-001', product_code='PLC', taxonomy_version_id='T1',
                         source_hash='hash-1', input_payload={})
    _complete(repo, run)
    current = repo.get_latest('ITR-001')
    old = current['review']['customer_task']
    new = {**old, 'value': '人工确认后的任务', 'review_status': 'CONFIRMED', 'reviewer_edit': '人工确认后的任务'}
    repo.save_field_review('ITR-001', field_name='customer_task', action='EDITED', old=old, new=new,
                           reviewer='质量专家', analysis_status='IN_REVIEW')
    repo.save_scene_review('ITR-001', scene_match={
        'scene_match_status': 'NOT_MATCHED', 'matched_scene_id': '',
        'match_reason': '现有场景未覆盖', 'missing_condition': '异常掉电条件缺失',
    }, reviewer='质量专家', analysis_status='IN_REVIEW')
    latest = repo.get_latest('ITR-001')
    assert latest['ai']['customer_task']['value'] == '掉电后计数保持'
    assert latest['review']['customer_task']['value'] == '人工确认后的任务'
    assert latest['match_reviewed'] is True
    assert latest['scene_match_status'] == 'NOT_MATCHED'
    with repo.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM reverse_quality_human_review').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM reverse_quality_scene_match').fetchone()[0] == 2
