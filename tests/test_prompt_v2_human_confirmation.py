from quality_knowledge.response_normalizer import normalize_stage_response


def test_occurrence_v2_normalizes_open_questions_and_system_scope():
    result = normalize_stage_response('occurrence', {
        'root_cause_summary': {'value': '疑似版本约束缺失', 'evidence_type': 'INFERRED', 'confidence': .5},
        'introduced_phase': 'design',
        'system_scope': {'affected_component': 'PLC adapter'},
        'open_questions': [{'question_key': 'VERSION', 'question': '是否只影响特定版本组合？', 'priority': 'high', 'answer_type': 'boolean'}],
    })
    assert result['introduced_phase'] == 'DESIGN'
    assert result['system_scope']['affected_component'] == 'PLC adapter'
    assert result['open_questions'][0]['priority'] == 'HIGH'
    assert result['open_questions'][0]['answer_type'] == 'BOOLEAN'


def test_recurrence_v2_keeps_customer_impact_and_limits_questions():
    result = normalize_stage_response('recurrence', {
        'recurrence_risk_level': 'HIGH',
        'customer_impact': {'impact_level': 'HIGH', 'visible_symptom': '业务中断'},
        'open_questions': [{'question': f'问题{i}'} for i in range(6)],
    })
    assert result['customer_impact']['visible_symptom'] == '业务中断'
    assert len(result['open_questions']) == 3


def test_occurrence_native_contract_keeps_mrc_lifecycle_and_issue_tags():
    result = normalize_stage_response('occurrence', {
        'occurrence_category': 'IMPLEMENTATION',
        'mrc': {'code': 'CHANGE_IMPACT_NOT_ASSESSED', 'label_zh': '变更影响未评估', 'control_status': 'NOT_EXECUTED', 'source_type': 'AI_INFERRED', 'confidence': .55},
        'lifecycle_tags': [{'code': 'DEVELOPMENT', 'source_type': 'AI_STANDARDIZED', 'confidence': .8}],
        'issue_type_tags': ['COMPATIBILITY'],
    })
    assert result['mrc']['code'] == 'CHANGE_IMPACT_NOT_ASSESSED'
    assert result['mrc']['control_status'] == 'NOT_EXECUTED'
    assert result['lifecycle_tags'][0]['code'] == 'DEVELOPMENT'
    assert result['issue_type_tags'][0]['code'] == 'COMPATIBILITY'


def test_old_stage_result_is_compatible_with_native_contract():
    occurrence = normalize_stage_response('occurrence', {'occurrence_category': 'DESIGN', 'introduced_phase': 'DESIGN', 'confidence': .7})
    escape = normalize_stage_response('escape', {'escape_category': 'RELEASE_GATE', 'actual_detection_stage': 'DELIVERY', 'confidence': .6})
    assert occurrence['mrc']['code'] == 'DESIGN'
    assert occurrence['lifecycle_tags'][0]['code'] == 'DESIGN'
    assert escape['mrc']['code'] == 'RELEASE_GATE'
    assert escape['lifecycle_tags'][0]['code'] == 'DELIVERY'
