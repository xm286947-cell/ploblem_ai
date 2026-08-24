import json
from pathlib import Path

from fastapi.testclient import TestClient

from quality_knowledge.repositories import IssueKnowledgeRepository
from quality_knowledge.web.app import create_app
from quality_knowledge.web.statistics_presenter import present_common_gaps


def _seed_issue(db, *, kid, issue_id, category, escape_category, gap_category=None):
    repo = IssueKnowledgeRepository(db)
    with repo.connect() as c:
        c.execute(
            "INSERT INTO quality_issue VALUES(?,?,?,?, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (kid, 'PLC', issue_id, f'{kid}-V1'),
        )
        normalized = {
            'occurrence': {'cause_l1': category},
            'escape': {'escape_l1': escape_category},
        }
        c.execute(
            "INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,normalized_json,title,product,platform) VALUES(?,?,?,?,?,?,?,?)",
            (f'{kid}-V1', kid, 1, kid, json.dumps(normalized, ensure_ascii=False), f'Title {issue_id}', 'P1', 'Platform'),
        )
        if gap_category:
            c.execute(
                "INSERT INTO analysis_run(analysis_run_id,knowledge_id,issue_version_id,analysis_type,status) VALUES(?,?,?,?,?)",
                (f'RUN-{kid}', kid, f'{kid}-V1', 'capability_gap', 'COMPLETED'),
            )
            c.execute(
                "INSERT INTO issue_capability_gap(gap_id,analysis_run_id,knowledge_id,issue_version_id,dimension,category,description,recommended_action) VALUES(?,?,?,?,?,?,?,?)",
                (f'GAP-{kid}', f'RUN-{kid}', kid, f'{kid}-V1', 'TECHNICAL', gap_category, '缺口说明', '建议动作'),
            )
    return repo


def test_statistics_reads_actual_source_cause_fields_and_does_not_fake_missing_rows(tmp_path):
    db = tmp_path / 'quality.db'
    repo = _seed_issue(db, kid='K1', issue_id='I-1', category='设计防护', escape_category='测试方法')
    stats = repo.statistics()
    assert stats['top_occurrence_causes'] == [{'category': '设计防护', 'count': 1}]
    assert stats['top_escape_causes'] == [{'category': '测试方法', 'count': 1}]


def test_capability_details_are_html_and_common_gap_link_keeps_exact_issue_ids(tmp_path):
    db = tmp_path / 'quality.db'
    _seed_issue(db, kid='K1', issue_id='I-1', category='设计防护', escape_category='测试方法', gap_category='测试能力')
    client = TestClient(create_app(db))
    response = client.get('/insights/capability-gaps?dimension=TECHNICAL')
    assert response.status_code == 200
    assert '能力缺口详情' in response.text
    assert 'Raw JSON' not in response.text

    view = present_common_gaps([{
        'category': '测试能力', 'dimension': 'TECHNICAL', 'business_type_count': 2,
        'business_types': 'PLC,HMI', 'related_issue_count': 2, 'related_issues': 'I-1,I-2',
    }])
    assert 'business_issue_ids=I-1%2CI-2' in view['items'][0]['drilldown_url']

    views = present_common_gaps([
        {'category': 'A', 'dimension': 'TECHNICAL', 'business_type_count': 1,
         'related_issue_count': 2, 'related_issues': 'I-1,I-2', 'related_knowledge_ids': 'K1,K2'},
        {'category': 'B', 'dimension': 'TECHNICAL', 'business_type_count': 1,
         'related_issue_count': 1, 'related_issues': 'I-3', 'related_knowledge_ids': 'K3'},
    ])
    assert 'knowledge_ids=K1%2CK2' in views['items'][0]['drilldown_url']
    assert 'knowledge_ids=K3' in views['items'][1]['drilldown_url']


def test_issue_list_exact_common_gap_scope_does_not_show_unrelated_rows(tmp_path):
    db = tmp_path / 'quality.db'
    _seed_issue(db, kid='K1', issue_id='I-1', category='设计防护', escape_category='测试方法')
    _seed_issue(db, kid='K2', issue_id='I-2', category='设计防护', escape_category='测试方法')
    client = TestClient(create_app(db))
    response = client.get('/issues?business_issue_ids=I-1')
    assert response.status_code == 200
    assert 'I-1' in response.text
    assert 'I-2' not in response.text
