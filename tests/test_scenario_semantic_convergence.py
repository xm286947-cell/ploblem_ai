from fastapi.testclient import TestClient

from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.web.app import create_app


def test_semantic_dictionary_is_seeded_and_has_review_principles(tmp_path):
    repository=ScenarioRepository(tmp_path/'semantic.db')
    dictionary=repository.semantic_dictionary('PLC')
    assert {'TYPICAL_PROBLEM','QUALITY_CONCERN','ENVIRONMENT_CONDITION'}==set(dictionary['types'])
    assert any(x['term_code']=='DATA_LOSS' for x in dictionary['typical_problem'])
    assert any('AI新增候选' in x for x in dictionary['principles'])


def test_scenario_persists_customer_language_and_operating_conditions(tmp_path):
    client=TestClient(create_app(tmp_path/'scenario.db'));repository=client.app.state.scenario_repository
    response=client.post('/quality-scenarios/save',data={
        'scenario_code':'SEM-1','name':'掉电后关键数据恢复','product_code':'PLC','activity_code':'POWER_LOSS_RETENTION_RECOVERY','status':'IN_REVIEW',
        'primary_typical_problem_code':'DATA_LOSS','secondary_typical_problem_codes':'RECOVERY_FAILED',
        'primary_quality_concern_code':'DATA_INTEGRITY','secondary_quality_concern_codes':'EXCEPTION_RECOVERABILITY',
        'primary_customer_experience_statement':'我希望设备掉电重启后关键计数仍然存在，不要清零，否则生产批次会失真。',
        'secondary_customer_experience_statements':'我希望恢复过程自动完成，不要依赖现场人工重配。',
        'environment_condition_codes':['POWER_CYCLE','REPEATED_OPERATION'],'operating_environment':'PLC与持久化存储','operating_condition':'设备正常生产后突然断电',
        'duration_frequency':'连续运行8小时后反复掉电10次','disturbances':'供电中断','extreme_conditions':'快速重复上下电',
    },follow_redirects=False)
    assert response.status_code==303
    item=repository.scenario(response.headers['location'].rsplit('/',1)[-1])
    assert item['primary_typical_problem_code']=='DATA_LOSS'
    assert item['secondary_quality_concern_codes']==['EXCEPTION_RECOVERABILITY']
    assert item['environment_condition_codes']==['POWER_CYCLE','REPEATED_OPERATION']
    assert item['operating_condition']=='设备正常生产后突然断电'
    page=client.get(response.headers['location'])
    assert '客户痛点与质量体验' in page.text and '环境与运行工况' in page.text and '掉电重启后关键计数仍然存在' in page.text
    assert page.text.index('关键场景判断') < page.text.index('客户痛点与质量体验') < page.text.index('标准质量模型与工程映射')
    insights=repository.insights(status='IN_REVIEW')
    assert insights['activity_typical_problem_matrix']['rows']
    assert any(cell['issue_count']==0 for row in insights['environment_typical_problem_matrix']['rows'] for cell in row['cells'].values())


def test_ai_candidate_must_be_reviewed_before_formal_dictionary(tmp_path):
    repository=ScenarioRepository(tmp_path/'review.db')
    code=repository.save_semantic_term({'term_type':'TYPICAL_PROBLEM','term_code':'CUSTOM_BLOCK','label_zh':'客户任务被阻塞','definition':'客户任务不能继续','inclusion_criteria':'任务无法继续','exclusion_criteria':'仅响应慢','product_code':'PLC','status':'CANDIDATE'})
    assert code=='CUSTOM_BLOCK'
    candidate=next(x for x in repository.semantic_dictionary('PLC')['items'] if x['term_code']==code)
    assert candidate['status']=='CANDIDATE'
    assert not any(x['term_code']==code for x in repository.semantic_dictionary('PLC',False)['items'])
    repository.review_semantic_term(candidate['term_id'],'MERGE','OPERATION_BLOCKED')
    with repository.connect() as c:row=c.execute('SELECT status,merged_into_code FROM scenario_semantic_term WHERE term_id=?',(candidate['term_id'],)).fetchone()
    assert tuple(row)==('MERGED','OPERATION_BLOCKED')


def test_semantic_dictionary_page_supports_form_and_candidate_review(tmp_path):
    client=TestClient(create_app(tmp_path/'web.db'))
    page=client.get('/settings/scenario-semantics?product_code=PLC')
    assert page.status_code==200 and '场景语义词典' in page.text and '新增正式词条' in page.text and '排除条件' in page.text
