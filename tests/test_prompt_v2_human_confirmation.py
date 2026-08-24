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
