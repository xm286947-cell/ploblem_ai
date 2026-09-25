from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

STATE_LOCK = threading.Lock()
TOTAL_CALLS = 0
STAGE_CALLS: dict[str, int] = {}


def _post_json(url: str, obj: Any, headers: dict[str, str] | None = None, timeout: float = 5.0):
    data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST', headers={'Content-Type':'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers, r.read()


def _find_page(payload: dict[str, Any]) -> tuple[int, str, str]:
    pages = payload.get('pages') or []
    if pages and isinstance(pages[0], dict):
        x = pages[0]
        return int(x.get('page') or 1), str(x.get('text') or ''), str(x.get('source_id') or payload.get('primary_source_id') or 'primary')
    return 1, str(payload.get('page_text') or ''), str(payload.get('primary_source_id') or 'primary')


def _unwrap_storage_envelope(body: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Support both the old Storage HTTP adapter body and Runtime-owned provider body.

    Runtime-owned provider execution serializes the Storage AgentRequest input as the
    user message, so business instructions/schema live inside that JSON envelope.
    """
    messages = body.get("messages") or []
    user_raw = str((messages[1] if len(messages) > 1 else {}).get("content") or "{}")
    try:
        outer = json.loads(user_raw)
    except Exception:
        outer = {}
    if not isinstance(outer, dict):
        outer = {}
    instructions = str(outer.get("instructions") or "")
    inner = outer.get("provider_payload")
    payload = inner if isinstance(inner, dict) else outer
    schema = outer.get("schema") if isinstance(outer.get("schema"), dict) else {}
    return instructions, payload, schema


def _stage_name(body: dict[str, Any]) -> str:
    messages = body.get("messages") or []
    system = str((messages[0] if messages else {}).get("content") or "")
    instructions, _payload, _schema = _unwrap_storage_envelope(body)
    marker = system + "\n" + instructions
    if "identify basic device metadata" in marker:
        return "identify_basic"
    if "identify DOCUMENT VERSION metadata" in marker:
        return "identify_version"
    if "identify concrete manufacturer model numbers" in marker:
        return "identify_models"
    if "extract storage-device datasheet facts in ONE pass" in marker:
        return "emmc_parameter_extract"
    if "final review and correction agent" in marker:
        return "final_review"
    if "storage-device engineering analysis assistant" in marker:
        return "technical_analysis"
    if "specification-extraction agent" in marker:
        return "legacy_extract"
    return "other"


def _stage_payload(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get('messages') or []
    system = str((messages[0] if messages else {}).get('content') or '')
    instructions, payload, envelope_schema = _unwrap_storage_envelope(body)
    marker_text = system + "\n" + instructions

    if 'identify basic device metadata' in marker_text:
        page, text, _sid = _find_page(payload)
        return {
            'vendor': {'value':'Demo Storage','page':page,'quote':'Vendor: Demo Storage','confidence':0.99},
            'model': {'value':'SYN-EMMC-1','page':page,'quote':'Model: SYN-EMMC-1','confidence':0.99},
            'device_type': {'value':'eMMC','page':page,'quote':'SYNTHETIC eMMC SPECIFICATION','confidence':0.99},
        }

    if 'identify DOCUMENT VERSION metadata' in marker_text:
        empty = {'value':'','page':0,'quote':'','confidence':0.0}
        return {
            'document_number': dict(empty), 'revision': dict(empty), 'revision_date': dict(empty),
            'document_status': dict(empty), 'document_variant': dict(empty),
            'language': {'value':'English','page':0,'quote':'','confidence':0.99},
        }

    if 'identify concrete manufacturer model numbers' in marker_text:
        page, text, _sid = _find_page(payload)
        return {'models':[{'value':'SYN-EMMC-1','scope':'product family','page':page,'quote':'Model: SYN-EMMC-1','confidence':0.99}]}

    if 'extract storage-device datasheet facts in ONE pass' in marker_text:
        marker = 'JSON Schema: '
        schema = dict(envelope_schema)
        if not schema and marker in marker_text:
            try: schema = json.loads(marker_text.split(marker,1)[1])
            except Exception: schema = {}
        enum = (((schema.get('properties') or {}).get('fields') or {}).get('items') or {}).get('properties',{}).get('field_key',{}).get('enum',[])
        page, text, sid = _find_page(payload)
        found = {
            'manufacturer': ('Demo Storage', None, 'Vendor: Demo Storage'),
            'product_family': ('SYN-EMMC-1', None, 'Model: SYN-EMMC-1'),
            'covered_part_numbers': ('SYN-EMMC-1', None, 'Model: SYN-EMMC-1'),
            'capacity': ('64', 'GB', 'Capacity: 64 GB'),
            'emmc_version': ('5.1', None, 'Interface: eMMC 5.1'),
            'device_life_time_est_typ_a': ('supported', None, 'Device Life Time Estimation A: supported'),
            'pre_eol_info': ('supported', None, 'PRE_EOL_INFO: supported'),
            # Legacy aliases remain for non-Runtime regression tests.
            'life_time_a': ('supported', None, 'Device Life Time Estimation A: supported'),
            'pre_eol': ('supported', None, 'PRE_EOL_INFO: supported'),
        }
        diagnostic = {
            'device_life_time_est_typ_a','device_life_time_est_typ_b','pre_eol_info','bkops_status',
            'vendor_proprietary_health_report','vendor_health_monitoring','access_method',
            'bad_block_count','erase_cycle_count','erase_cycle_granularity',
        }
        requirements = {'reliable_write','bkops','cache','sanitize','power_off_notification','error_reporting','field_firmware_update'}
        fields=[]
        for key in enum:
            if key in found:
                value, unit, quote = found[key]
                fields.append({
                    'field_key':key,'value':value,'unit':unit,'condition':None,
                    'scope_type':'product_family','scope_values':['SYN-EMMC-1'],
                    'evidence':{'source_id':sid,'page':page,'section':'','quote':quote},
                    'conflict_evidence':[],'confidence':0.99,'status':'found','derived':False,
                    'knowledge_type':'diagnostic_capability' if key in diagnostic else ('device_requirement' if key in requirements else 'specification'),
                })
            else:
                fields.append({
                    'field_key':key,'value':None,'unit':None,'condition':None,
                    'scope_type':'product_family','scope_values':[],
                    'evidence':None,'conflict_evidence':[],'confidence':0.0,'status':'missing','derived':False,
                    'knowledge_type':'diagnostic_capability' if key in diagnostic else ('device_requirement' if key in requirements else 'specification'),
                })
        return {'fields':fields}

    if 'final review and correction agent' in marker_text:
        return {'overall_status':'ready_for_human_review','summary':'Mock review completed.','findings':[],'corrections':[]}

    if 'storage-device engineering analysis assistant' in marker_text:
        evidence = payload.get('evidence') or []
        if evidence:
            eid = str(evidence[0].get('id'))
            return {
                'conclusion':'基于当前已核验证据生成的 Mock 分析。',
                'facts':[{'text':'当前存在可追溯的已核验证据。','evidence_ids':[eid]}],
                'inferences':[],'software_impacts':[],'historical_context':[],
                'unknowns':['真实模型质量不在 Mock 验收范围内'],
            }
        return {'conclusion':'证据不足。','facts':[],'inferences':[],'software_impacts':[],'historical_context':[],'unknowns':['无证据']}

    if 'specification-extraction agent' in marker_text:
        return {'candidates':[]}

    return {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path == '/health':
            raw=json.dumps({'status':'ok','service':'storage-mock-router'}).encode()
            self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_response(404); self.end_headers(); return
        n=int(self.headers.get('Content-Length','0'))
        raw=self.rfile.read(n)
        try: body=json.loads(raw.decode('utf-8'))
        except Exception:
            self.send_response(400); self.end_headers(); return

        global TOTAL_CALLS
        stage = _stage_name(body)
        with STATE_LOCK:
            TOTAL_CALLS += 1
            call_no = TOTAL_CALLS
            STAGE_CALLS[stage] = STAGE_CALLS.get(stage, 0) + 1
            stage_call_no = STAGE_CALLS[stage]

        payload = _stage_payload(body)
        scenario = f'storage-web-{stage}-{stage_call_no}-{call_no}-{uuid4().hex[:8]}'
        fault = os.environ.get('STORAGE_MOCK_FAULT','normal').strip().lower()
        behavior: dict[str, Any] = {}
        # Fault injection is deliberately targeted at the dedicated eMMC extraction
        # call. Metadata identification remains healthy so the product flow reaches
        # storage.emmc.parameter_extract before Runtime retry/budget behavior is tested.
        target = stage == 'emmc_parameter_extract'
        if fault == '429_once' and target and stage_call_no == 1:
            behavior = {'status':429, 'retry_after':'0'}
        elif fault == 'truncate_once' and target and stage_call_no == 1:
            behavior = {'truncate_at':80}
        elif fault == 'persistent_503' and target:
            behavior = {'status':503}
        upstream = os.environ.get('OPENAI_MOCK_UPSTREAM','http://127.0.0.1:18000').rstrip('/')
        try:
            _post_json(upstream + '/__mock__/scenario', {'scenario_key':scenario,'payload':json.dumps(payload,ensure_ascii=False,separators=(',',':')),'behavior':behavior})
            status, hdrs, resp = _post_json(
                upstream + '/v1/chat/completions', body,
                headers={'Authorization':'Bearer mock-key','X-Mock-Scenario-Key':scenario}, timeout=10,
            )
        except urllib.error.HTTPError as e:
            status=e.code; resp=e.read(); hdrs=e.headers
        except Exception as e:
            resp=json.dumps({'error':{'message':str(e),'type':'router_error','code':'router_error'}}).encode(); status=502; hdrs={}
        self.send_response(status)
        self.send_header('Content-Type', hdrs.get('Content-Type','application/json'))
        self.send_header('Content-Length', str(len(resp)))
        self.end_headers(); self.wfile.write(resp)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=18001)
    a=ap.parse_args(); server=ThreadingHTTPServer((a.host,a.port),Handler)
    print(f'Storage Mock Router listening on http://{a.host}:{a.port}/v1', flush=True)
    try: server.serve_forever(0.1)
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
