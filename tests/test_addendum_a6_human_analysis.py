from quality_knowledge.human_analysis import HumanAnalysisRepository,HumanAnalysisService
def test_dynamic_fields_values_audit_restart(tmp_path):
 db=tmp_path/'k.db';s=HumanAnalysisService(HumanAnalysisRepository(db))
 f1=s.create_field(field_key='conclusion',field_name='结论',field_type='LONG_TEXT',required=True)
 f2=s.create_field(field_key='risk',field_name='风险',field_type='SINGLE_SELECT',options=['高','中','低'])
 f3=s.create_field(field_key='actions',field_name='措施',field_type='MULTI_SELECT',options=['测试','设计'])
 f4=s.create_field(field_key='confirmed',field_name='确认',field_type='BOOLEAN')
 f5=s.create_field(field_key='score',field_name='评分',field_type='NUMBER')
 f6=s.create_field(field_key='review_date',field_name='日期',field_type='DATE')
 a=s.save_analysis('K1','V1',{f1['field_id']:'人工结论',f2['field_id']:'高',f3['field_id']:['测试','设计'],f4['field_id']:'on',f5['field_id']:'8.5',f6['field_id']:'2026-08-21'})
 assert a['values'][f1['field_id']]=='人工结论' and a['values'][f3['field_id']]==['测试','设计']
 s2=HumanAnalysisService(HumanAnalysisRepository(db));b=s2.get_analysis('K1','V1')
 assert b['values'][f4['field_id']] is True and b['values'][f5['field_id']]==8.5
 s2.save_analysis('K1','V1',{f1['field_id']:'修改后',f2['field_id']:'中',f3['field_id']:[],f4['field_id']:None,f5['field_id']:'9',f6['field_id']:'2026-08-21'})
 assert len(s2.get_audit(a['analysis_id']))>=2
def test_field_definition_separate_from_issue_schema(tmp_path):
 db=tmp_path/'k.db';s=HumanAnalysisService(HumanAnalysisRepository(db))
 s.create_field(field_key='text',field_name='文本',field_type='TEXT')
 import sqlite3
 with sqlite3.connect(db) as c:
  tables={x[0] for x in c.execute("select name from sqlite_master where type='table'")}
  assert 'human_analysis_field_definition' in tables
  assert 'quality_issue' not in tables
