import re
from datetime import date
class HumanAnalysisService:
 def __init__(self,repository):self.repository=repository
 def list_field_definitions(self,enabled_only=False):return self.repository.list_fields(enabled_only)
 def create_field(self,**kwargs):return self.repository.create_field(**kwargs)
 def update_field(self,field_id,**kwargs):return self.repository.update_field(field_id,**kwargs)
 def delete_field(self,field_id):return self.repository.delete_field(field_id)
 def create_option(self,field_id,option_value,option_label=None,display_order=0,enabled=True):
  return self.repository.create_option(field_id,option_value,option_label,display_order,enabled)
 def update_option(self,option_id,option_label=None,display_order=None,enabled=None):
  return self.repository.update_option(option_id,option_label,display_order,enabled)
 def option_is_used(self,field_id,option_value):return self.repository.option_is_used(field_id,option_value)
 def get_analysis(self,knowledge_id,issue_version_id):return self.repository.get_analysis(knowledge_id,issue_version_id)
 def get_audit(self,analysis_id):return self.repository.list_audit(analysis_id)
 def _validate(self,f,v):
  t=f['field_type'];empty=v is None or v=='' or v==[]
  if f['required'] and empty:raise ValueError('REQUIRED:'+f['field_name'])
  if empty:return None
  if t=='MULTI_SELECT':
   if not isinstance(v,list):v=[v]
   if any(x not in {o['option_value'] for o in f['options'] if o['enabled']} for x in v):raise ValueError('INVALID_OPTION')
  elif t=='SINGLE_SELECT' and v not in {o['option_value'] for o in f['options'] if o['enabled']}:raise ValueError('INVALID_OPTION')
  elif t=='BOOLEAN':v=v in (True,'true','1','on','yes')
  elif t=='NUMBER':
   try:v=float(v)
   except:raise ValueError('INVALID_NUMBER')
  elif t=='DATE':
   try:date.fromisoformat(str(v))
   except:raise ValueError('INVALID_DATE')
  if f.get('validation_rule') and t in {'TEXT','LONG_TEXT'} and not re.search(f['validation_rule'],str(v)):raise ValueError('VALIDATION_RULE_FAILED')
  return v
 def save_analysis(self,knowledge_id,issue_version_id,values,changed_by='web'):
  fs={x['field_id']:x for x in self.repository.list_fields(True)}
  return self.repository.save_values(knowledge_id,issue_version_id,{fid:self._validate(f,values.get(fid,f.get('default_value'))) for fid,f in fs.items()},changed_by)

 def query_issue_ids(self,field_id=None,value=None):
  return [x['knowledge_id'] for x in self.repository.query_analyses(field_id,value)]
 def export_rows(self,issue_keys=None):return self.repository.export_rows(issue_keys)
