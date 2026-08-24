import json
from pathlib import Path
from openpyxl import Workbook
from fastapi.testclient import TestClient
from builder.ai_client import AIResponse
from quality_knowledge.web import create_app

class WorkbenchClient:
    def complete(self,messages):
        sys=messages[0]['content']
        if '发生原因分析器' in sys:
            data={'root_cause_summary':'变更逻辑遗漏异常输入处理','failure_mechanism':'异常输入进入错误分支','contributing_factors':['变更影响分析不足'],'occurrence_category':'CHANGE','confidence':0.9,'evidence':[]}
        elif '流出原因分析器' in sys:
            data={'escape_cause_summary':'测试未覆盖变更路径','verification_gap':'缺少异常场景用例','process_gap':'变更影响未进入必测清单','escape_category':'TEST_GAP','confidence':0.85,'evidence':[]}
        elif '再发风险分析器' in sys:
            data={'recurrence_risk_level':'HIGH','recurrence_risk_reason':'当前措施仍偏单点修复','existing_control_coverage':'仅修改当前代码','residual_risk':'其他模块仍存在同类遗漏风险','is_common_issue':True,'potential_affected_products':['PLC','HMI'],'horizontal_action_needed':True}
        else:
            data={'capability_gaps':[
                {'dimension':'TECHNICAL','category':'TEST_CAPABILITY','description':'缺少异常变更场景验证能力','recommended_control':'补充自动化异常场景回归','confidence':0.83,'evidence':[]},
                {'dimension':'MANAGEMENT','category':'CHANGE_MANAGEMENT','description':'缺少变更影响闭环','recommended_control':'建立变更影响检查清单','confidence':0.88,'evidence':[]},
                {'dimension':'GOVERNANCE','category':'COMMON_TEST_ASSET','description':'缺少跨产品公共变更测试资产','recommended_control':'沉淀公共回归测试集','confidence':0.8,'evidence':[]}
            ]}
        return AIResponse(json.dumps(data,ensure_ascii=False),'workbench-model',{})

def _seed(client,tmp_path):
    x=tmp_path/'plc.xlsx';w=Workbook();s=w.active
    s.append(['ITR 单号','问题描述','问题原因定位（×开发填写×）','问题解决方案（×开发填写×）','是否漏测','产品','平台','严重程度'])
    s.append(['ITR-RC4-1','在线修改后通信异常','变更逻辑遗漏','修复代码','是','PLC','IDE','A']);w.save(x)
    with x.open('rb') as f:
        r=client.post('/api/issues/import',files={'file':('plc.xlsx',f,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},data={'business_type':'PLC'})
    assert r.status_code==200
    return client.get('/api/issues').json()['items'][0]['knowledge_id']

def test_rc4_issue_detail_is_business_workbench(tmp_path, monkeypatch):
    app=create_app(tmp_path/'q.db'); c=TestClient(app); kid=_seed(c,tmp_path)
    svc=app.state.knowledge_issue_service
    svc.run_issue_analysis(kid,Path(__file__).parents[1],client=WorkbenchClient())
    html=c.get('/issues/'+kid).text
    assert '问题事实' in html
    assert '为什么发生' in html and '为什么流出' in html
    assert '再发防控' in html and 'HIGH' in html
    assert '技术能力缺口' in html and '管理能力缺口' in html and '横向治理需求' in html
    assert 'TEST_CAPABILITY' in html and 'CHANGE_MANAGEMENT' in html and 'COMMON_TEST_ASSET' in html
    assert 'Original · 原始 Excel 数据' in html and 'Normalized · 数据库标准化数据' in html
    assert f'action="/analysis/{kid}"' in html

def test_rc4_issue_list_surfaces_ai_status_risk_and_gap_summary(tmp_path):
    app=create_app(tmp_path/'q.db'); c=TestClient(app); kid=_seed(c,tmp_path)
    app.state.knowledge_issue_service.run_issue_analysis(kid,Path(__file__).parents[1],client=WorkbenchClient())
    html=c.get('/issues').text
    assert '再发风险' in html and 'HIGH' in html
    assert '能力缺口' in html
    assert 'COMPLETED' in html
    assert 'T 1' in html and 'M 1' in html and 'G 1' in html
