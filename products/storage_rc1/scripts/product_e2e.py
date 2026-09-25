from __future__ import annotations
import argparse, json, mimetypes, os, time, uuid
from pathlib import Path
from urllib import request

EXPECTED_AGENT = "storage.emmc.parameter_extract"
EXPECTED_FIELDS = 37


def assess_review_gate(extraction):
    """Classify a frozen Mock E2E review gate without changing product semantics."""
    review_queue=list(extraction.get('review_queue') or [])
    allowed_review_types={'diagnostic_unresolved'}
    unexpected=[item for item in review_queue if str(item.get('type') or '') not in allowed_review_types]
    if extraction.get('review_required') and unexpected:
        return {'classification':'unexpected','queue':review_queue,'unexpected':unexpected}
    if extraction.get('review_required') and not review_queue:
        return {'classification':'invalid_empty','queue':review_queue,'unexpected':[]}
    if extraction.get('review_required'):
        return {'classification':'expected_human_review','queue':review_queue,'unexpected':[]}
    return {'classification':'clean','queue':review_queue,'unexpected':[]}


def http_json(url, method='GET', body=None, headers=None, timeout=20):
    data=None
    if body is not None:
        if isinstance(body,(dict,list)):
            data=json.dumps(body,ensure_ascii=False).encode(); headers={**(headers or {}),'Content-Type':'application/json'}
        else:
            data=body
    req=request.Request(url,data=data,method=method,headers=headers or {})
    with request.urlopen(req,timeout=timeout) as r:
        raw=r.read(); return r.status, json.loads(raw.decode() or '{}')


def multipart(fields, file_field, file_path):
    boundary='----StorageE2E'+uuid.uuid4().hex
    out=bytearray()
    for k,v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    p=Path(file_path); ctype=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
    out += f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{p.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
    out += p.read_bytes()+b'\r\n'; out += f'--{boundary}--\r\n'.encode()
    return bytes(out), {'Content-Type':f'multipart/form-data; boundary={boundary}'}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base',default='http://127.0.0.1:8765'); ap.add_argument('--pdf',default='examples/synthetic_emmc.pdf'); ap.add_argument('--timeout',type=int,default=360)
    a=ap.parse_args(); base=a.base.rstrip('/'); pdf=Path(a.pdf)
    if not pdf.is_absolute(): pdf=Path(__file__).resolve().parents[1]/pdf
    result={'base':base,'pdf':str(pdf),'fault':os.getenv('STORAGE_MOCK_FAULT','normal'),'steps':[]}
    def step(name, fn):
        t=time.time(); value=fn(); result['steps'].append({'name':name,'seconds':round(time.time()-t,3),'result':value}); print('PASS',name); return value

    step('health', lambda: http_json(base+'/api/health')[1])
    data,h=multipart({},'file',pdf)
    ident=step('identify', lambda: http_json(base+'/api/documents/identify','POST',data,h,a.timeout)[1])
    fields={
        'vendor': ident.get('vendor',{}).get('value') or 'Demo Storage',
        'model': ident.get('model',{}).get('value') or 'SYN-EMMC-1',
        'device_type': ident.get('device_type',{}).get('value') or 'eMMC',
        'original_url':'','publisher':'Demo','models_json':json.dumps(ident.get('models') or [],ensure_ascii=False),
        'document_number':'','revision':'','revision_date':'','document_variant':''
    }
    data,h=multipart(fields,'file',pdf)
    job=step('submit_import', lambda: http_json(base+'/api/documents/jobs','POST',data,h,min(a.timeout, 120))[1])
    deadline=time.time()+a.timeout; last=None
    while time.time()<deadline:
        last=http_json(base+'/api/documents/jobs/'+job['job_id'])[1]
        if last['status'] in ('completed','failed'): break
        time.sleep(.4)
    if not last or last['status']!='completed': raise SystemExit('IMPORT_FAILED '+json.dumps(last,ensure_ascii=False))
    imp=last['result']; result['import']=imp; print('PASS import completed')
    device_id=imp['device_id']

    facts=step('runtime_37_facts', lambda: http_json(base+f'/api/devices/{device_id}/runtime-facts')[1])
    if facts.get('expected_field_count') != EXPECTED_FIELDS or facts.get('field_count') != EXPECTED_FIELDS:
        raise SystemExit('EMMC_37_FIELD_CONTRACT_NOT_ACTIVE '+json.dumps({'field_count':facts.get('field_count'),'expected':facts.get('expected_field_count')},ensure_ascii=False))
    fact_keys=[str(x.get('field_key') or '') for x in facts.get('facts') or []]
    contract=step('runtime_contract', lambda: http_json(base+'/api/v1/runtime/contract/emmc')[1])
    if fact_keys != contract.get('fields'):
        raise SystemExit('EMMC_FIELD_ORDER_MISMATCH')
    found=[x for x in facts.get('facts') or [] if x.get('status')=='found']
    if not found:
        raise SystemExit('NO_FOUND_FACTS')
    if not any((x.get('resolved_evidence') or {}).get('page') and (x.get('resolved_evidence') or {}).get('quote') for x in found):
        raise SystemExit('NO_37_FIELD_EVIDENCE_TRACE')
    source=facts.get('source') or {}
    if not source.get('sha256'):
        raise SystemExit('NO_SOURCE_SHA256')

    extraction=step('extraction_status', lambda: http_json(base+f'/api/devices/{device_id}/extraction-status')[1])
    # Review Gate is a legitimate business state when deterministic coverage leaves
    # diagnostic fields unresolved.  The E2E harness must not erase that state or
    # require fake values just to make the test green.  Other gate causes remain
    # unexpected for this frozen Mock Golden path and still fail closed.
    review_gate=assess_review_gate(extraction)
    if review_gate['classification']=='unexpected':
        raise SystemExit('UNEXPECTED_REVIEW_GATE_REQUIRED '+json.dumps({'unexpected':review_gate['unexpected'],'extraction':extraction},ensure_ascii=False))
    if review_gate['classification']=='invalid_empty':
        raise SystemExit('INVALID_EMPTY_REVIEW_GATE '+json.dumps(extraction,ensure_ascii=False))
    result['review_gate']={
        'required':bool(extraction.get('review_required')),
        'queue':review_gate['queue'],
        'accepted_as_business_review':review_gate['classification']=='expected_human_review',
    }
    review=step('final_review_status', lambda: http_json(base+f'/api/devices/{device_id}/final-review')[1])
    allowed_final_review={'not_run','not_required','ready_for_human_review'}
    if review_gate['classification']=='expected_human_review':
        # A deterministic Review Gate may legitimately block Final Review until
        # the human confirmation step is completed.  Preserve that product gate.
        allowed_final_review.add('blocked')
    if review.get('overall_status') not in allowed_final_review:
        raise SystemExit('UNEXPECTED_FINAL_REVIEW_STATUS '+json.dumps(review,ensure_ascii=False))
    candidates=step('candidates', lambda: http_json(base+f'/api/devices/{device_id}/candidates')[1])
    if not candidates: raise SystemExit('NO_CANDIDATES')
    if not any((x.get('source_page') or 0)>0 and x.get('source_text') for x in candidates): raise SystemExit('NO_EVIDENCE_TRACE')
    spec_status=step('specification_status', lambda: http_json(base+f'/api/devices/{device_id}/specification-status')[1])
    if spec_status.get('status') not in {'pending_confirmation','partially_confirmed','confirmed','attention_required'}:
        raise SystemExit('SPECIFICATION_WORKFLOW_NOT_CREATED '+json.dumps(spec_status,ensure_ascii=False))
    reviewed=step('reviewed_specifications', lambda: http_json(base+f'/api/devices/{device_id}/reviewed-specifications')[1])
    if not reviewed:
        raise SystemExit('NO_REVIEWED_SPECIFICATION_DRAFT')
    status=step('runtime_status', lambda: http_json(base+'/api/v1/runtime/status')[1])
    executions=step('runtime_executions', lambda: http_json(base+'/api/v1/runtime/executions')[1])
    items=executions.get('items') or []
    if not items: raise SystemExit('NO_RUNTIME_EXECUTION_EVIDENCE')
    if not all((x.get('provider_calls') or 0)>=1 for x in items): raise SystemExit('INVALID_PROVIDER_CALL_ACCOUNTING')
    if not any(x.get('agent_id') == EXPECTED_AGENT for x in items):
        raise SystemExit('DEDICATED_EMMC_RUNTIME_AGENT_NOT_USED')

    mock_calls=None
    if os.getenv('STORAGE_PRODUCT_TEST_MODE','').strip().lower() == 'mock':
        counters=http_json('http://127.0.0.1:18000/__mock__/counters')[1].get('data') or {}
        mock_calls=sum(int(v) for v in counters.values())
        provider_calls=sum(int(x.get('provider_calls') or 0) for x in items)
        if mock_calls != provider_calls:
            raise SystemExit(f'HTTP_PROVIDER_ACCOUNTING_MISMATCH mock={mock_calls} runtime={provider_calls}')
        fault=os.getenv('STORAGE_MOCK_FAULT','normal')
        dedicated=[x for x in items if x.get('agent_id') == EXPECTED_AGENT]
        if not dedicated:
            raise SystemExit('DEDICATED_EMMC_RUNTIME_AGENT_NOT_USED')
        dedicated_call_counts=[int(x.get('provider_calls') or 0) for x in dedicated]
        expected_calls = 2 if fault in {'truncate_once','429_once'} else 1
        if fault in {'truncate_once','429_once'}:
            # Fault injection targets the first detailed extraction call. A later
            # Targeted Supplement may legitimately use the same agent with one call,
            # so checking dedicated[-1] is incorrect. Require evidence that recovery
            # actually consumed two provider calls, while preserving the hard budget.
            valid=(expected_calls in dedicated_call_counts and all(1 <= n <= expected_calls for n in dedicated_call_counts))
        else:
            valid=all(n == expected_calls for n in dedicated_call_counts)
        if not valid:
            raise SystemExit(
                'DEDICATED_RUNTIME_PROVIDER_CALLS_UNEXPECTED '+
                json.dumps({'fault':fault,'expected_observed':expected_calls,'actual_sequence':dedicated_call_counts},ensure_ascii=False)
            )
        result['accounting']={
            'mock_requests':mock_calls,
            'runtime_provider_calls':provider_calls,
            'dedicated_agent_provider_calls':dedicated_call_counts,
            'fault_recovery_observed': expected_calls in dedicated_call_counts,
            'match':True,
        }

    result.update({
        'device_id':device_id,
        'candidate_count':len(candidates),
        'review_status':review.get('overall_status'),
        'specification_status':spec_status,
        'reviewed_specification_count':len(reviewed),
        'runtime':status,
        'runtime_executions':items,
        'runtime_37_facts':{
            'field_count':facts.get('field_count'),
            'counts':facts.get('counts'),
            'source':facts.get('source'),
            'unresolved_evidence':facts.get('unresolved_evidence'),
        },
    })
    out=Path(__file__).resolve().parents[1]/'release'/'PRODUCT_E2E_RESULT.json'; out.parent.mkdir(exist_ok=True); out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PRODUCT_E2E PASS',out)

if __name__=='__main__': main()
