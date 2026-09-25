import json

import httpx
import pytest

from storage_life import ai, core


def mock_client(payload):
    def handler(request):
        assert request.url.path == '/v1/responses'
        body = json.loads(request.content)
        assert body['store'] is False
        assert body['text']['format']['strict'] is True
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(payload)}]}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ai_extract_checks_literal_page_quote(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, 'Endurance (TBW): 600 TB for 512 GB model', 'text')]
    payload = {'candidates': [
        {'canonical_name': 'tbw', 'value': '600', 'unit': 'TB',
         'quote': 'Endurance (TBW): 600 TB for 512 GB model', 'condition': '512 GB', 'confidence': 0.9},
        {'canonical_name': 'capacity', 'value': '1', 'unit': 'TB',
         'quote': 'Capacity: 1 TB', 'condition': '', 'confidence': 0.9}]}
    found, selected = ai.extract_candidates(pages, 'SSD', 'Example', 'X', mock_client(payload))
    assert selected == [1]
    assert len(found) == 1
    assert found[0]['canonical_name'] == 'tbw'
    assert found[0]['source_page'] == 1
    assert found[0]['condition'] == '512 GB'


def test_ai_import_keeps_candidate_pending(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'DB', tmp_path / 'test.sqlite3')
    monkeypatch.setattr(core, 'extract_pdf', lambda data: [(1, 'Endurance: 600 TB', 'text')])
    monkeypatch.setattr(ai, 'extract_specification_once', lambda *args, **kwargs: {
        'candidates': [{
            'canonical_name': 'tbw', 'parameter_name': 'TBW', 'ai_value': '600', 'ai_unit': 'TB',
            'condition': '', 'scope': 'product family', 'source_id': kwargs.get('source_id', 's1'),
            'source_page': 1, 'source_section': '', 'source_text': 'Endurance: 600 TB',
            'confidence': .6, 'extraction_method': 'agent_single_pass',
            'evidence': [{'source_id': kwargs.get('source_id', 's1'), 'source_page': 1, 'source_section': '',
                          'source_text': 'Endurance: 600 TB', 'confidence': .6, 'extraction_method': 'agent_single_pass', 'scope': 'product family'}],
        }],
        'analyzed_pages': [1], 'document_identity': {}, 'models': [], 'schema_valid': True,
        'model_calls': 1, 'unresolved_evidence': [], 'review_required': False, 'review_queue': [],
    })
    imported = core.import_document('x.pdf', b'%PDF-test', 'X', 'Y', 'SSD')
    assert imported['extraction_mode'] == 'agent_single_pass_generic'
    candidate = core.list_candidates(imported['device_id'])[0]
    assert candidate['verify_status'] == 'pending'
    assert core.confirmed([imported['device_id']]) == []


def test_ai_claims_require_valid_citations(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(ai.knowledge, 'search', lambda q, limit: {'items': [
        {'kind': 'verified_knowledge', 'fact': 'TBW is a total bytes written endurance rating.',
         'evidence': {'source_id': 's1', 'page': 2}}]})
    monkeypatch.setattr(ai, '_case_evidence', lambda q: [])
    payload = {'conclusion': 'TBW 需要结合写入负载评估。',
               'facts': [{'text': 'TBW 是耐久度指标。', 'evidence_ids': ['e1']}],
               'inferences': [], 'software_impacts': [], 'historical_context': [], 'unknowns': []}
    result = ai.analyze('TBW', client=mock_client(payload))
    assert result['status'] == 'draft_for_review'
    assert result['facts'][0]['evidence_ids'] == ['e1']
    payload['facts'][0]['evidence_ids'] = ['missing']
    with pytest.raises(ai.AIResponseError):
        ai.analyze('TBW', client=mock_client(payload))


def test_raw_nand_agent_schema_contains_requirement_fields():
    fields = ai.FIELDS_BY_DEVICE['Raw NAND']
    for name in ('nand_type','page_size','block_size','pages_per_block','pe_cycles',
                 'ecc_requirement','bad_block_requirement','program_time','erase_time','retention'):
        assert name in fields


def test_openai_compatible_gemma_agent(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'http://gemma.local/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'gemma-e4b')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    payload = {'candidates': [
        {'canonical_name': 'pe_cycles', 'value': '100K', 'unit': 'cycles',
         'quote': 'P/E cycles with ECC: 100K', 'condition': 'with ECC', 'confidence': 0.93},
        {'canonical_name': 'program_time', 'value': '400', 'unit': 'us',
         'quote': 'Page Program time: 400us typical', 'condition': 'typical', 'confidence': 0.9},
    ]}
    def handler(request):
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(payload)}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pages=[(4, 'P/E cycles with ECC: 100K\nPage Program time: 400us typical', 'text')]
    found, selected = ai.extract_candidates(pages, 'Raw NAND', 'GigaDevice', 'GD5F1GQ5UExxG', client)
    assert selected == [4]
    assert {x['canonical_name'] for x in found} == {'pe_cycles','program_time'}
    assert all(x['extraction_method'] == 'agent_text' for x in found)


def test_deepseek_sets_json_mode_and_8192_budget(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'deepseek-chat')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    seen = {}
    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{"candidates":[]}'}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    found, _ = ai.extract_candidates([(1, 'No matching storage parameters here.', 'text')],
                                     'Raw NAND', 'X', 'Y', client)
    assert found == []
    assert seen['max_tokens'] == 8192
    assert seen['response_format'] == {'type':'json_object'}
    assert 'enable_thinking' not in seen
    assert ai.status()['profile'] == 'deepseek'


def test_qwen_json_mode_disables_thinking(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://example.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'qwen-plus')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    seen = {}
    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{"candidates":[]}'}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ai.extract_candidates([(1, 'No matching parameters.', 'text')], 'eMMC', 'X', 'Y', client)
    assert seen['max_tokens'] == 8192
    assert seen['response_format'] == {'type':'json_object'}
    assert seen['enable_thinking'] is False
    assert ai.status()['profile'] == 'qwen'


def test_length_finish_reason_is_retried_with_smaller_chunks(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'deepseek-chat')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    calls = []
    evidence = 'P/E cycles with ECC: 100K'
    page_text = ('background text\n' * 260) + evidence + ('\nmore text' * 260)
    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'])
        excerpt = payload['page_text']
        calls.append(len(excerpt))
        if len(excerpt) > 3000:
            return httpx.Response(200, json={'choices':[{'finish_reason':'length','message':{'content':'{"candidates": ['}}]})
        candidates = []
        if evidence in excerpt:
            candidates.append({'canonical_name':'pe_cycles','value':'100K','unit':'cycles',
                               'quote':evidence,'condition':'with ECC','confidence':0.9})
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps({'candidates':candidates})}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    found, _ = ai.extract_candidates([(4, page_text, 'text')], 'Raw NAND', 'GigaDevice', 'GD5F', client)
    assert any(size > 3000 for size in calls)
    assert any(size <= 3000 for size in calls)
    assert any(x['canonical_name'] == 'pe_cycles' for x in found)


def test_generic_agent_does_not_force_json_mode(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'http://model.local/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'custom-model')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    seen = {}
    def handler(request):
        body = json.loads(request.content)
        seen.update(body)
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{"candidates":[]}'}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ai.extract_candidates([(1, 'text', 'text')], 'SSD', 'X', 'Y', client)
    assert 'response_format' not in seen
    assert 'enable_thinking' not in seen


def test_yaml_agent_profile_is_loaded(tmp_path, monkeypatch):
    for name in ('STORAGE_LIFE_AGENT_BASE_URL','STORAGE_LIFE_AGENT_MODEL','STORAGE_LIFE_AGENT_API_KEY',
                 'OPENAI_API_KEY','DEEPSEEK_API_KEY','DASHSCOPE_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    cfg = tmp_path / 'agent.yaml'
    cfg.write_text('''\nagent:\n  enabled: true\n  active_profile: qwen\n  profiles:\n    qwen:\n      provider: qwen\n      protocol: openai_compatible_chat\n      base_url: https://dashscope.example/v1\n      model: qwen-test\n      api_key: yaml-secret\n      max_output_tokens: 4096\n      json_mode: auto\n      disable_thinking: true\n''', encoding='utf-8')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_CONFIG', str(cfg))
    status = ai.status()
    assert status['configured'] is True
    assert status['profile'] == 'qwen'
    assert status['profile_name'] == 'qwen'
    assert status['model'] == 'qwen-test'
    assert status['max_output_tokens'] == 4096
    assert status['json_mode'] is True
    assert status['config_source'] == str(cfg)


def test_environment_agent_overrides_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / 'agent.yaml'
    cfg.write_text('''\nagent:\n  enabled: true\n  active_profile: qwen\n  profiles:\n    qwen:\n      provider: qwen\n      base_url: https://dashscope.example/v1\n      model: qwen-yaml\n''', encoding='utf-8')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_CONFIG', str(cfg))
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'deepseek-env')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    status = ai.status()
    assert status['profile'] == 'deepseek'
    assert status['profile_name'] == 'environment'
    assert status['model'] == 'deepseek-env'
    assert status['config_source'] == 'environment'


def test_disabled_yaml_keeps_legacy_openai_fallback(tmp_path, monkeypatch):
    cfg = tmp_path / 'agent.yaml'
    cfg.write_text('''\nagent:\n  enabled: false\n  active_profile: deepseek\n  profiles:\n    deepseek:\n      provider: deepseek\n      base_url: https://api.deepseek.com/v1\n      model: deepseek-chat\n''', encoding='utf-8')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_CONFIG', str(cfg))
    monkeypatch.delenv('STORAGE_LIFE_AGENT_BASE_URL', raising=False)
    monkeypatch.delenv('STORAGE_LIFE_AGENT_MODEL', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'legacy-test')
    status = ai.status()
    assert status['profile'] == 'openai'
    assert status['profile_name'] == 'legacy_openai'


def test_agent_identifies_device_metadata_with_evidence(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, 'GigaDevice\nGD5F1GQ5UExxG\n1Gb SPI NAND Flash Memory', 'text'),
             (2, 'Features and ordering information', 'text')]
    payload = {
        'vendor': {'value': 'GigaDevice', 'page': 1, 'quote': 'GigaDevice', 'confidence': 0.99},
        'model': {'value': 'GD5F1GQ5UExxG', 'page': 1, 'quote': 'GD5F1GQ5UExxG', 'confidence': 0.98},
        'device_type': {'value': 'Raw NAND', 'page': 1, 'quote': '1Gb SPI NAND Flash Memory', 'confidence': 0.97},
    }
    result = ai.identify_device(pages, mock_client(payload))
    assert result['vendor']['value'] == 'GigaDevice'
    assert result['model']['value'] == 'GD5F1GQ5UExxG'
    assert result['device_type']['value'] == 'NAND Flash'
    assert result['analyzed_pages'] == [1, 2]


def test_agent_identification_drops_unsupported_metadata(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, 'Example Storage Datasheet', 'text')]
    payload = {
        'vendor': {'value': 'InventedVendor', 'page': 1, 'quote': 'InventedVendor', 'confidence': 0.99},
        'model': {'value': '', 'page': 0, 'quote': '', 'confidence': 0},
        'device_type': {'value': 'SSD', 'page': 1, 'quote': 'Example Storage Datasheet', 'confidence': 0.5},
    }
    result = ai.identify_device(pages, mock_client(payload))
    assert result['vendor']['value'] == ''
    assert result['model']['value'] == ''
    assert result['device_type']['value'] == 'SSD'


def test_gigadevice_nand_template_read_plan():
    from storage_life import templates
    pages = [
        (1, 'GD5F1GQ5xExxG DATASHEET', 'text'),
        (4, '1 FEATURE\n1Gb SLC NAND Flash\nP/E cycles with ECC: 100K', 'text'),
        (5, '2 GENERAL DESCRIPTION\nDensity is 1Gb\nGigaDevice SPI NAND', 'text'),
        (6, '2.1 VALID PART NUMBERS\nProduct Number Density Voltage Package Type Temperature', 'text'),
        (10, '4 ARRAY ORGANIZATION\n64 pages per block', 'text'),
        (48, '12.4 ASSISTANT BAD BLOCK MANAGEMENT\nBad Block Mark', 'text'),
        (51, '12.7 INTERNAL ECC\n4bits /528byte', 'text'),
        (58, '18 PERFORMANCE AND TIMING\ntPROG tBERS', 'text'),
        (60, '19 ORDERING INFORMATION\n1G: 1Gb', 'text'),
    ]
    plan = templates.build_read_plan(pages, 'NAND Flash', 'GigaDevice')
    by_page = {x['page']: x for x in plan}
    assert 'pages_per_block' in by_page[10]['target_fields']
    assert 'bad_block_mark' in by_page[48]['target_fields']
    assert 'ecc_capability' in by_page[51]['target_fields']
    assert {'program_time', 'erase_time'} <= set(by_page[58]['target_fields'])
    assert 'capacity' in by_page[60]['target_fields']
    summary = templates.template_summary('Raw NAND', 'GigaDevice')
    assert summary['device_type'] == 'NAND Flash'
    assert summary['vendor_template'] == 'gigadevice'
    assert summary['vendor_template_supported'] is True


def test_duplicate_capacity_evidence_is_merged(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'deepseek-chat')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body['messages'][1]['content'])
        text = payload['page_text']
        candidates = []
        if '1Gb SLC NAND Flash' in text:
            candidates.append({'canonical_name':'capacity','value':'1Gb','unit':'',
                'quote':'1Gb SLC NAND Flash','condition':'','scope':'GD5F1GQ5xExxG family','confidence':0.94})
        if 'Density is 1Gbit' in text:
            candidates.append({'canonical_name':'capacity','value':'1Gbit','unit':'',
                'quote':'Density is 1Gbit','condition':'','scope':'GD5F1GQ5xExxG family','confidence':0.97})
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'candidates':candidates})}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    pages = [(4, '1 FEATURE\n1Gb SLC NAND Flash', 'text'),
             (5, '2 GENERAL DESCRIPTION\nDensity is 1Gbit', 'text')]
    found, selected = ai.extract_candidates(pages, 'NAND Flash', 'GigaDevice', 'GD5F1GQ5xExxG', client)
    capacity = [x for x in found if x['canonical_name'] == 'capacity']
    assert selected == [4, 5]
    assert len(capacity) == 1
    assert len(capacity[0]['evidence']) == 2
    assert {e['source_page'] for e in capacity[0]['evidence']} == {4, 5}


def test_internal_device_vocabulary():
    from storage_life import templates
    assert templates.normalize_device_type('Raw NAND') == 'NAND Flash'
    assert templates.normalize_device_type('SPI NAND') == 'NAND Flash'
    assert templates.normalize_device_type('SPI NOR') == 'NOR Flash'
    assert templates.supported_vendor_template('Macronix', 'NAND Flash') is True
    assert templates.supported_vendor_template('ATMEL', 'NOR Flash') is True
    assert templates.supported_vendor_template('ATMEL', 'NAND Flash') is False
    assert templates.supported_vendor_template('SkyHigh Memory', 'eMMC') is True
    assert templates.supported_vendor_template('TIMAR', 'SSD') is True


def test_identify_models_supports_multiple_part_numbers(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, 'VALID PART NUMBERS\nGD5F1GQ5UExxG 3.3V\nGD5F1GQ5RExxG 1.8V', 'text')]
    payload = {'models': [
        {'value': 'GD5F1GQ5UExxG', 'scope': '3.3V family', 'page': 1,
         'quote': 'GD5F1GQ5UExxG 3.3V', 'confidence': 0.97},
        {'value': 'GD5F1GQ5RExxG', 'scope': '1.8V family', 'page': 1,
         'quote': 'GD5F1GQ5RExxG 1.8V', 'confidence': 0.96},
    ]}
    result = ai.identify_models(pages, 'NAND Flash', 'GigaDevice', 'GD5F1GQ5xExxG', mock_client(payload))
    assert [x['value'] for x in result['models']] == ['GD5F1GQ5UExxG', 'GD5F1GQ5RExxG']
    assert {x['scope'] for x in result['models']} == {'3.3V family', '1.8V family'}


def test_final_review_keeps_deterministic_missing_fields(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    payload = {'overall_status': 'ready_for_human_review', 'summary': 'No direct conflicts.', 'findings': []}
    candidates = [{'id': 'c1', 'canonical_name': 'capacity', 'ai_value': '1Gb', 'ai_unit': '',
                   'condition': '', 'scope': 'product family', 'source_page': 1,
                   'source_section': 'overview', 'source_text': 'Density is 1Gb', 'evidence': []}]
    result = ai.final_review('NAND Flash', 'GigaDevice', 'GD5F', [], candidates, mock_client(payload))
    assert 'capacity' not in result['missing_fields']
    assert 'ecc_capability' in result['missing_fields']
    assert result['overall_status'] == 'attention_required'


def test_final_review_returns_only_valid_candidate_corrections(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    payload = {
        'overall_status': 'attention_required', 'summary': 'Scope needs correction.', 'findings': [],
        'corrections': [
            {'candidate_id':'c1','action':'update','proposed_value':'2.7-3.6V','proposed_unit':'V',
             'proposed_condition':'','proposed_scope':'3.3V family','reason':'Direct evidence.'},
            {'candidate_id':'not-present','action':'update','proposed_value':'x','proposed_unit':'',
             'proposed_condition':'','proposed_scope':'','reason':'invalid id'},
        ]
    }
    candidates = [{'id':'c1','canonical_name':'voltage','ai_value':'2.7','ai_unit':'V','condition':'',
                   'scope':'product family','source_page':1,'source_section':'Electrical',
                   'source_text':'Operating Voltage: 2.7-3.6V','evidence':[
                       {'source_page':1,'source_section':'Electrical','source_text':'Operating Voltage: 2.7-3.6V','scope':'3.3V family'}]}]
    result = ai.final_review('NAND Flash','GigaDevice','GD5F',[],candidates,mock_client(payload))
    assert len(result['corrections']) == 1
    assert result['corrections'][0]['candidate_id'] == 'c1'
    assert result['corrections'][0]['proposed_scope'] == '3.3V family'



def _deepseek_review_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_final_review_truncation_auto_splits_batches(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'deepseek-chat')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    calls=[]
    def handler(request):
        body=json.loads(request.content)
        payload=json.loads(body['messages'][1]['content'])
        groups=payload.get('field_groups') or []
        calls.append(len(groups))
        if len(groups)>1:
            return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"overall_status":'}}]})
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'overall_status':'ready_for_human_review','summary':'ok','findings':[],'corrections':[]})}}]})
    client=_deepseek_review_client(handler)
    candidates=[]
    for i,field in enumerate(['capacity','pe_cycles','program_time','erase_time','retention']):
        candidates.append({'id':f'c{i}','canonical_name':field,'ai_value':'1','ai_unit':'',
                           'condition':'','scope':'product family','source_page':1,'source_text':'1','evidence':[]})
    result=ai.final_review('NAND Flash','GigaDevice','GD5F',[],candidates,client)
    assert result['review_meta']['retry_count']>0
    assert result['review_meta']['failed_fields']==[]
    assert any(x>1 for x in calls) and any(x==1 for x in calls)


def test_final_review_isolates_one_bad_json_field(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('STORAGE_LIFE_AGENT_BASE_URL', 'https://api.deepseek.com/v1')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_MODEL', 'deepseek-chat')
    monkeypatch.setenv('STORAGE_LIFE_AGENT_API_KEY', 'test')
    def handler(request):
        body=json.loads(request.content)
        payload=json.loads(body['messages'][1]['content'])
        fields=[g.get('field') for g in payload.get('field_groups') or []]
        if 'ecc_requirement' in fields:
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"broken":'}}]})
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'overall_status':'ready_for_human_review','summary':'ok','findings':[],'corrections':[]})}}]})
    client=_deepseek_review_client(handler)
    candidates=[
        {'id':'c1','canonical_name':'capacity','ai_value':'1Gb','ai_unit':'','condition':'','scope':'product family','source_page':1,'source_text':'1Gb','evidence':[]},
        {'id':'c2','canonical_name':'ecc_requirement','ai_value':'4 bits/528 bytes','ai_unit':'','condition':'','scope':'product family','source_page':2,'source_text':'4 bits/528 bytes','evidence':[]},
    ]
    result=ai.final_review('NAND Flash','GigaDevice','GD5F',[],candidates,client)
    assert 'ecc_requirement' in result['review_meta']['failed_fields']
    assert result['overall_status']=='attention_required'
    assert any(f['canonical_name']=='ecc_requirement' for f in result['findings'])


def _single_pass_payload(device_type, found=None):
    """Build a complete contract response; unspecified fields are legitimate missing."""
    found = found or {}
    _, keys = ai._single_pass_schema(device_type)
    fields = []
    for key in keys:
        item = {
            'field_key': key, 'value': None, 'unit': None, 'condition': None,
            'scope_type': 'product_family', 'scope_values': [], 'evidence': None,
            'conflict_evidence': [], 'confidence': 0, 'status': 'missing', 'derived': False,
        }
        item.update(found.get(key) or {})
        fields.append(item)
    return {'fields': fields}


def test_single_pass_extractor_calls_model_once_and_resolves_evidence(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, '# Page 1\nTIMAR 97 Series SSD\nTBW: 600 TB\nSMART / Health supported', 'markdown_text')]
    found = {
        'manufacturer': {'value':'TIMAR','status':'found','confidence':.99,
                         'evidence':{'source_id':'pdf1','page':1,'section':'cover','quote':'TIMAR'},
                         'conflict_evidence':[]},
        'product_family': {'value':'97 Series','status':'found','confidence':.99,
                           'evidence':{'source_id':'pdf1','page':1,'section':'cover','quote':'97 Series SSD'},
                           'conflict_evidence':[]},
        'covered_part_numbers': {'value':'97-512','status':'found','confidence':.99,
                                 'scope_values':['97-512'],
                                 'evidence':{'source_id':'pdf1','page':1,'section':'cover','quote':'97 Series SSD'},
                                 'conflict_evidence':[]},
        'tbw': {'value':'600','unit':'TB','status':'found','confidence':.95,
                'evidence':{'source_id':'pdf1','page':1,'section':'Endurance','quote':'TBW: 600 TB'},
                'conflict_evidence':[]},
        'smart_health': {'value':'Supported','status':'found','confidence':.9,
                         'evidence':{'source_id':'pdf1','page':1,'section':'Health','quote':'SMART / Health supported'},
                         'conflict_evidence':[]},
    }
    payload = _single_pass_payload('SSD', found)
    calls = {'n': 0}
    def handler(request):
        calls['n'] += 1
        return httpx.Response(200, json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(payload)}]}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ai.extract_specification_once(pages, 'SSD', 'TIMAR', '97 Series', source_id='pdf1', client=client)
    assert calls['n'] == 1
    assert result['model_calls'] == 1
    assert result['schema_valid'] is True
    assert result['unresolved_evidence'] == []
    assert result['review_required'] is False
    tbw = next(x for x in result['candidates'] if x['canonical_name'] == 'tbw')
    assert tbw['source_id'] == 'pdf1'
    assert 'TBW: 600 TB' in tbw['source_text']


def test_single_pass_contract_adapter_accepts_legacy_name(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, '# Page 1\nTBW: 600 TB\nSMART supported', 'markdown_text')]
    payload = _single_pass_payload('SSD', {
        'tbw': {'value':'600','unit':'TB','status':'found','confidence':.9,
                'evidence':{'source_id':'p','page':1,'section':'x','quote':'TBW: 600 TB'}, 'conflict_evidence':[]},
        'smart_health': {'value':'Supported','status':'found','confidence':.9,
                         'evidence':{'source_id':'p','page':1,'section':'x','quote':'SMART supported'}, 'conflict_evidence':[]},
    })
    # Simulate a provider contract drift: field_key -> name. Adapter may reshape only.
    tbw = next(x for x in payload['fields'] if x['field_key'] == 'tbw')
    tbw['name'] = tbw.pop('field_key')
    result = ai.extract_specification_once(pages, 'SSD', 'TIMAR', '97', source_id='p', client=mock_client(payload))
    assert any(x['canonical_name'] == 'tbw' and x['ai_value'] == '600' for x in result['candidates'])


def test_multi_source_conflict_preserves_both_provenances(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pdf_pages = [(1, '# Page 1\nPower Loss Protection: No', 'markdown_text')]
    web_pages = [(1, '# Page 1\nPower Loss Protection: Supported', 'markdown_text')]
    payload = _single_pass_payload('SSD', {
        'plp': {'value':None,'status':'conflict','confidence':.99,
                'evidence':None,
                'conflict_evidence':[
                    {'source_id':'pdf','page':1,'section':'Feature','quote':'Power Loss Protection: No'},
                    {'source_id':'web','page':1,'section':'Product page','quote':'Power Loss Protection: Supported'},
                ]},
        # Keep critical fields non-missing so this test isolates conflict behavior.
        'tbw': {'value':'600','unit':'TB','status':'found','confidence':.9,
                'evidence':{'source_id':'pdf','page':1,'section':'Feature','quote':'Power Loss Protection: No'}, 'conflict_evidence':[]},
        'smart_health': {'value':'Supported','status':'found','confidence':.9,
                         'evidence':{'source_id':'web','page':1,'section':'Product page','quote':'Power Loss Protection: Supported'}, 'conflict_evidence':[]},
    })
    # Values above deliberately use locators that resolve; semantic value support is not a resolver responsibility.
    result = ai.extract_specification_bundle_once([
        {'source_id':'pdf','pages':pdf_pages}, {'source_id':'web','pages':web_pages}
    ], 'SSD', 'TIMAR', '97 Series', client=mock_client(payload))
    conflict = next(x for x in result['facts'] if x['field_key'] == 'plp')
    assert conflict['status'] == 'conflict'
    assert {x['source_id'] for x in conflict['resolved_conflict_evidence']} == {'pdf','web'}
    q = next(x for x in result['review_queue'] if x.get('field_key') == 'plp')
    assert q['type'] == 'conflict'
    assert {x['source_id'] for x in q['evidence']} == {'pdf','web'}


def test_review_gate_ignores_noncritical_missing(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    pages = [(1, '# Page 1\nTBW: 600 TB\nSMART supported', 'markdown_text')]
    payload = _single_pass_payload('SSD', {
        'manufacturer': {'value':'TIMAR','status':'found','confidence':.99,
                         'evidence':{'source_id':'p','page':1,'section':'x','quote':'TBW: 600 TB'}, 'conflict_evidence':[]},
        'covered_part_numbers': {'value':'97-512','status':'found','confidence':.99,'scope_values':['97-512'],
                                 'evidence':{'source_id':'p','page':1,'section':'x','quote':'TBW: 600 TB'}, 'conflict_evidence':[]},
        'tbw': {'value':'600','unit':'TB','status':'found','confidence':.9,
                'evidence':{'source_id':'p','page':1,'section':'x','quote':'TBW: 600 TB'}, 'conflict_evidence':[]},
        'smart_health': {'value':'Supported','status':'found','confidence':.9,
                         'evidence':{'source_id':'p','page':1,'section':'x','quote':'SMART supported'}, 'conflict_evidence':[]},
    })
    result = ai.extract_specification_once(pages, 'SSD', 'TIMAR', '97', source_id='p', client=mock_client(payload))
    assert result['review_required'] is False
    assert not any(x['type'] == 'critical_missing' for x in result['review_queue'])
