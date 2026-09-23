import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from quality_knowledge.reverse_quality_store import ReverseQualityResult, SQLiteReverseQualityRepository


def _complete(repo, run, value='掉电后计数保持', missing_information=None):
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
        missing_information=missing_information,
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
        run_seq=3, started_at='2026-09-20T10:00:00', completed_at='2026-09-20T10:00:01',
    )
    assert json.loads(dto.to_json()) == dto.to_dict()
    assert dto.to_json() == dto.to_json()
    assert dto.run_seq == 3
    assert dto.started_at
    assert dto.completed_at


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
    assert latest['run_seq'] == run1['run_seq']
    assert latest['started_at']
    assert latest['completed_at']
    assert latest['result']['run_seq'] == run1['run_seq']
    assert latest['result']['started_at']
    assert latest['result']['completed_at']
    assert latest['review']['customer_task']['value'] == '有效结果'
    runs = repo.list_runs('ITR-001')
    assert [row['status'] for row in runs] == ['FAILED', 'COMPLETED']


    run3 = repo.start_run(canonical_itr='ITR-001', product_code='HMI', taxonomy_version_id='T2',
                          source_hash='hash-3', input_payload={})
    repo.fail_run(run3['run_id'], 'new identity failed')
    latest = repo.get_latest('ITR-001')
    assert latest['product_code'] == 'PLC'
    assert latest['taxonomy_version_id'] == 'T1'
    assert latest['result']['identity']['product_code'] == 'PLC'
    assert latest['result']['identity']['taxonomy_version_id'] == 'T1'


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



def test_older_completion_cannot_replace_newer_valid_run(tmp_path):
    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    older = repo.start_run(canonical_itr='ITR-ORDER', product_code='PLC', taxonomy_version_id='T1',
                           source_hash='old', input_payload={})
    newer = repo.start_run(canonical_itr='ITR-ORDER', product_code='PLC', taxonomy_version_id='T1',
                           source_hash='new', input_payload={})
    _complete(repo, newer, '新结果')
    _complete(repo, older, '旧结果')
    latest = repo.get_latest('ITR-ORDER')
    assert latest['run_id'] == newer['run_id']
    assert latest['run_seq'] == newer['run_seq']
    assert latest['result']['run_seq'] == newer['run_seq']
    assert latest['review']['customer_task']['value'] == '新结果'


def test_patch58_legacy_data_migrates_idempotently(tmp_path):
    legacy_path = tmp_path / 'legacy.db'
    with sqlite3.connect(legacy_path) as connection:
        connection.executescript('''
        CREATE TABLE reverse_quality_analysis(
          canonical_itr TEXT PRIMARY KEY, source_hash TEXT NOT NULL, product_code TEXT NOT NULL,
          taxonomy_version_id TEXT, input_json TEXT NOT NULL, ai_json TEXT NOT NULL,
          review_json TEXT NOT NULL, scene_match_status TEXT NOT NULL,
          matched_scene_id TEXT, match_reason TEXT, missing_condition TEXT,
          status TEXT NOT NULL, model TEXT, error TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE reverse_quality_field_review(
          review_id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_itr TEXT NOT NULL, field_name TEXT NOT NULL,
          action TEXT NOT NULL, old_json TEXT NOT NULL, new_json TEXT NOT NULL, reviewer TEXT NOT NULL,
          reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE reverse_quality_match_review(
          review_id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_itr TEXT NOT NULL,
          old_json TEXT NOT NULL, new_json TEXT NOT NULL, reviewer TEXT NOT NULL,
          reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')
        ai = {'customer_task': {'value': '旧AI值', 'source_type': 'FACT', 'evidence_ids': ['cs.description'],
                                'confidence': 1.0, 'review_status': 'PENDING', 'reviewer_edit': ''}}
        reviewed = {'customer_task': {**ai['customer_task'], 'value': '人工值',
                                      'review_status': 'CONFIRMED', 'reviewer_edit': '人工值'}}
        payload = {'canonical_itr': 'ITR-LEGACY',
                   'evidence': {'cs.description': {'id': 'cs.description', 'value': '旧AI值'}}}
        connection.execute(
            '''INSERT INTO reverse_quality_analysis(
               canonical_itr,source_hash,product_code,taxonomy_version_id,input_json,ai_json,review_json,
               scene_match_status,matched_scene_id,match_reason,missing_condition,status,model,error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('ITR-LEGACY','legacy-hash','PLC','T1',json.dumps(payload),json.dumps(ai),json.dumps(reviewed),
             'NOT_MATCHED','','未覆盖','条件缺失','IN_REVIEW','legacy-model',''))
        connection.execute(
            '''INSERT INTO reverse_quality_field_review(
               canonical_itr,field_name,action,old_json,new_json,reviewer)
               VALUES(?,?,?,?,?,?)''',
            ('ITR-LEGACY','customer_task','EDITED',json.dumps(ai['customer_task']),
             json.dumps(reviewed['customer_task']),'质量专家'))
        connection.execute(
            '''INSERT INTO reverse_quality_match_review(
               canonical_itr,old_json,new_json,reviewer) VALUES(?,?,?,?)''',
            ('ITR-LEGACY',json.dumps({'scene_match_status':'NEED_REVIEW'}),
             json.dumps({'scene_match_status':'NOT_MATCHED','matched_scene_id':'',
                         'match_reason':'未覆盖','missing_condition':'条件缺失'}),'质量专家'))

    class LegacyRepository:
        def connect(self):
            connection = sqlite3.connect(legacy_path)
            connection.row_factory = sqlite3.Row
            return connection

    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    assert repo.migrate_legacy(LegacyRepository()) == 1
    latest = repo.get_latest('ITR-LEGACY')
    assert latest['ai']['customer_task']['value'] == '旧AI值'
    assert latest['review']['customer_task']['value'] == '人工值'
    assert latest['match_reviewed'] is True
    assert latest['scene_match_status'] == 'NOT_MATCHED'
    assert latest['status'] == 'IN_REVIEW'
    assert repo.migrate_legacy(LegacyRepository()) == 0



def test_missing_information_resolution_is_audited(tmp_path):
    repo = SQLiteReverseQualityRepository(tmp_path / 'reverse_quality_v01.db')
    run = repo.start_run(canonical_itr='ITR-MISSING', product_code='PLC', taxonomy_version_id='T1',
                         source_hash='hash-missing', input_payload={})
    _complete(repo, run, missing_information=[{
        'missing_id': 'RQM-TEST-1',
        'field_name': 'scale_or_load',
        'reason': '原始问题未提供规模',
        'question': '现场设备规模是多少？',
        'evidence_needed': ['现场拓扑或设备数量'],
    }])
    resolved = repo.resolve_missing_information(
        'ITR-MISSING', missing_id='RQM-TEST-1', status='CONFIRMED',
        answer='现场共 12 台设备', reviewer='质量专家')
    assert resolved['status'] == 'CONFIRMED'
    assert resolved['answer'] == '现场共 12 台设备'
    latest = repo.get_latest('ITR-MISSING')
    assert latest['missing_information'][0]['status'] == 'CONFIRMED'
    assert latest['missing_information'][0]['answer'] == '现场共 12 台设备'
    with repo.connect() as connection:
        audit = connection.execute(
            """SELECT target_type,field_name,action,reviewer
               FROM reverse_quality_human_review
               WHERE run_id=? ORDER BY review_id DESC LIMIT 1""",
            (run['run_id'],),
        ).fetchone()
    assert dict(audit) == {
        'target_type': 'MISSING_INFORMATION',
        'field_name': 'RQM-TEST-1',
        'action': 'CONFIRMED',
        'reviewer': '质量专家',
    }
