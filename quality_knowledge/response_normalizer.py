from __future__ import annotations
from typing import Any


def _clamp_confidence(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def normalize_evidence_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source_id = value.get('source_id') or value.get('source') or value.get('id')
    if not source_id:
        return None
    return {
        'source_type': str(value.get('source_type') or 'FIELD'),
        'source_id': str(source_id),
        'field_path': value.get('field_path') or value.get('path'),
        'excerpt': value.get('excerpt') or value.get('quote'),
    }


def normalize_evidence_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        ref = normalize_evidence_reference(item)
        if ref:
            out.append(ref)
    return out


def normalize_evidence_value(value: Any, *, confidence: Any = 0.0, reason: str = '', evidence: Any = None) -> dict[str, Any]:
    if value is None:
        value = ''
    if isinstance(value, dict):
        raw_value = value.get('value', value.get('text', value.get('summary', value.get('description', ''))))
        refs = value.get('source_refs', value.get('evidence_refs', value.get('evidence', evidence)))
        evidence_type = str(value.get('evidence_type') or 'UNKNOWN').upper()
        if evidence_type not in {'EXPLICIT', 'SUMMARIZED', 'INFERRED', 'UNKNOWN'}:
            evidence_type = 'UNKNOWN'
        return {
            'value': '' if raw_value is None else str(raw_value),
            'confidence': _clamp_confidence(value.get('confidence', confidence)),
            'evidence_type': evidence_type,
            'reason': str(value.get('reason') or reason or ''),
            'source_refs': normalize_evidence_list(refs),
        }
    if isinstance(value, (list, tuple)):
        value = '; '.join(str(x) for x in value if x is not None)
    return {
        'value': str(value),
        'confidence': _clamp_confidence(confidence),
        'evidence_type': 'UNKNOWN',
        'reason': str(reason or ''),
        'source_refs': normalize_evidence_list(evidence),
    }

def normalize_open_questions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list): return []
    out=[]
    for i,item in enumerate(value[:3]):
        if isinstance(item,str): item={'question':item}
        if not isinstance(item,dict) or not str(item.get('question') or '').strip(): continue
        priority=str(item.get('priority') or 'MEDIUM').upper(); answer_type=str(item.get('answer_type') or 'TEXT').upper()
        if priority not in {'HIGH','MEDIUM','LOW'}: priority='MEDIUM'
        if answer_type not in {'TEXT','BOOLEAN','SINGLE_SELECT'}: answer_type='TEXT'
        options=item.get('suggested_options') or []
        if isinstance(options,str): options=[options]
        out.append({'question_key':str(item.get('question_key') or f'QUESTION_{i+1}'),'question':str(item['question']).strip(),'why_it_matters':str(item.get('why_it_matters') or ''),'priority':priority,'answer_type':answer_type,'suggested_options':[str(x) for x in options[:6]]})
    return out


def normalize_occurrence(obj: dict[str, Any]) -> dict[str, Any]:
    confidence = obj.get('confidence', 0.0)
    evidence = obj.get('evidence', [])
    root = obj.get('root_cause_summary', obj.get('root_cause', obj.get('why_occurred', '')))
    mechanism = obj.get('failure_mechanism', obj.get('mechanism', ''))
    factors = obj.get('contributing_factors', obj.get('factors', [])) or []
    if not isinstance(factors, list):
        factors = [factors]
    return {
        'root_cause_summary': normalize_evidence_value(root, confidence=confidence, evidence=evidence),
        'failure_mechanism': normalize_evidence_value(mechanism, confidence=confidence, evidence=evidence),
        'contributing_factors': [normalize_evidence_value(x, confidence=confidence, evidence=evidence) for x in factors],
        'occurrence_category': str(obj.get('occurrence_category') or obj.get('category') or ''),
        'introduced_phase': str(obj.get('introduced_phase') or 'UNKNOWN').upper(),
        'system_scope': obj.get('system_scope') if isinstance(obj.get('system_scope'),dict) else {},
        'open_questions': normalize_open_questions(obj.get('open_questions')),
        'confidence': _clamp_confidence(confidence),
        'evidence': normalize_evidence_list(evidence),
    }


def normalize_escape(obj: dict[str, Any]) -> dict[str, Any]:
    confidence = obj.get('confidence', 0.0)
    evidence = obj.get('evidence', [])
    return {
        'escape_cause_summary': normalize_evidence_value(obj.get('escape_cause_summary', obj.get('why_escaped', obj.get('root_cause', ''))), confidence=confidence, evidence=evidence),
        'verification_gap': normalize_evidence_value(obj.get('verification_gap', ''), confidence=confidence, evidence=evidence),
        'process_gap': normalize_evidence_value(obj.get('process_gap', ''), confidence=confidence, evidence=evidence),
        'escape_category': str(obj.get('escape_category') or obj.get('category') or ''),
        'expected_detection_stage': str(obj.get('expected_detection_stage') or 'UNKNOWN').upper(),
        'actual_detection_stage': str(obj.get('actual_detection_stage') or 'UNKNOWN').upper(),
        'missing_control': str(obj.get('missing_control') or ''),
        'control_failure_type': str(obj.get('control_failure_type') or 'UNKNOWN').upper(),
        'open_questions': normalize_open_questions(obj.get('open_questions')),
        'confidence': _clamp_confidence(confidence),
        'evidence': normalize_evidence_list(evidence),
    }


def normalize_recurrence(obj: dict[str, Any]) -> dict[str, Any]:
    level = str(obj.get('recurrence_risk_level') or obj.get('risk_level') or 'UNKNOWN').upper()
    if level not in {'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'}:
        aliases = {'高': 'HIGH', '中': 'MEDIUM', '低': 'LOW'}
        level = aliases.get(level, 'UNKNOWN')
    confidence = obj.get('confidence', 0.0)
    evidence = obj.get('evidence', [])
    products = obj.get('potential_affected_products', obj.get('affected_products', [])) or []
    if isinstance(products, str):
        products = [products]
    return {
        'recurrence_risk_level': level,
        'recurrence_risk_reason': normalize_evidence_value(obj.get('recurrence_risk_reason', obj.get('risk_reason', '')), confidence=confidence, evidence=evidence),
        'existing_control_coverage': normalize_evidence_value(obj.get('existing_control_coverage', obj.get('control_coverage', '')), confidence=confidence, evidence=evidence),
        'residual_risk': normalize_evidence_value(obj.get('residual_risk', ''), confidence=confidence, evidence=evidence),
        'is_common_issue': bool(obj.get('is_common_issue', False)),
        'potential_affected_products': [str(x) for x in products],
        'horizontal_action_needed': bool(obj.get('horizontal_action_needed', False)),
        'customer_impact': obj.get('customer_impact') if isinstance(obj.get('customer_impact'),dict) else {},
        'open_questions': normalize_open_questions(obj.get('open_questions')),
    }


def normalize_capability_gap_item(x: dict[str, Any]) -> dict[str, Any]:
    dimension = str(x.get('dimension', x.get('gap_dimension', ''))).upper()
    aliases = {'TECH': 'TECHNICAL', 'TECHNOLOGY': 'TECHNICAL', 'MANAGE': 'MANAGEMENT', 'GOVERN': 'GOVERNANCE'}
    dimension = aliases.get(dimension, dimension)
    products = x.get('affected_products', []) or []
    if isinstance(products, str):
        products = [products]
    return {
        'gap_id': str(x.get('gap_id') or ''),
        'dimension': dimension,
        'category': str(x.get('category', x.get('gap_category', '')) or ''),
        'description': str(x.get('description', x.get('gap_description', '')) or ''),
        'why_needed': str(x.get('why_needed') or ''),
        'related_mechanism': str(x.get('related_mechanism', x.get('related_issue_mechanism', '')) or ''),
        'recommended_control': str(x.get('recommended_control') or x.get('recommended_action') or ''),
        'recommended_action': str(x.get('recommended_action') or x.get('recommended_control') or ''),
        'action_type': str(x.get('action_type') or ''),
        'action_target': str(x.get('action_target') or ''),
        'expected_prevention_effect': str(x.get('expected_prevention_effect') or ''),
        'scope': str(x.get('scope') or ''),
        'affected_products': [str(p) for p in products],
        'priority': str(x.get('priority') or 'P2').upper() if str(x.get('priority') or 'P2').upper() in {'P0','P1','P2'} else 'P2',
        'first_action': str(x.get('first_action') or ''),
        'verification_metric': str(x.get('verification_metric') or ''),
        'confidence': _clamp_confidence(x.get('confidence', 0.0)),
        'evidence': normalize_evidence_list(x.get('evidence', x.get('evidence_refs', []))),
    }


def normalize_stage_response(stage: str, obj: Any) -> Any:
    if stage == 'capability_gap':
        if isinstance(obj, list):
            values = obj
        elif isinstance(obj, dict):
            values = obj.get('capability_gaps', obj.get('gaps', []))
        else:
            values = []
        if isinstance(values, dict):
            values = [values]
        return {'capability_gaps': [normalize_capability_gap_item(x) for x in values if isinstance(x, dict)]}
    if not isinstance(obj, dict):
        raise ValueError(f'{stage} response must be a JSON object')
    if stage == 'occurrence':
        return normalize_occurrence(obj)
    if stage == 'escape':
        return normalize_escape(obj)
    if stage == 'recurrence':
        return normalize_recurrence(obj)
    return obj
