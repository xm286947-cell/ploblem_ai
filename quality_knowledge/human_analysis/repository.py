import sqlite3,json,uuid
from datetime import datetime,timezone
VALID_TYPES={'TEXT','LONG_TEXT','SINGLE_SELECT','MULTI_SELECT','BOOLEAN','NUMBER','DATE'}
def now(): return datetime.now(timezone.utc).isoformat()
class HumanAnalysisRepository:
 def __init__(self,db_path): self.db_path=str(db_path);self._schema()
 def connect(self):
  c=sqlite3.connect(self.db_path);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
 def _schema(self):
  with self.connect() as c:
   c.executescript("""
CREATE TABLE IF NOT EXISTS human_analysis_field_definition(field_id TEXT PRIMARY KEY,field_key TEXT UNIQUE NOT NULL,field_name TEXT NOT NULL,description TEXT,field_type TEXT NOT NULL,required INTEGER DEFAULT 0,enabled INTEGER DEFAULT 1,display_order INTEGER DEFAULT 0,default_value TEXT,validation_rule TEXT,config_version INTEGER DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS human_analysis_field_option(option_id TEXT PRIMARY KEY,field_id TEXT NOT NULL,option_value TEXT NOT NULL,option_label TEXT NOT NULL,display_order INTEGER DEFAULT 0,enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS human_analysis(analysis_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,created_by TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(knowledge_id,issue_version_id));
CREATE TABLE IF NOT EXISTS human_analysis_value(analysis_id TEXT NOT NULL,field_id TEXT NOT NULL,value_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(analysis_id,field_id));
CREATE TABLE IF NOT EXISTS human_analysis_audit(audit_id TEXT PRIMARY KEY,analysis_id TEXT NOT NULL,field_id TEXT,old_value_json TEXT,new_value_json TEXT,changed_by TEXT,changed_at TEXT NOT NULL,action TEXT NOT NULL);
""")
 def list_fields(self,enabled_only=False):
  with self.connect() as c:
   q='SELECT * FROM human_analysis_field_definition'+(' WHERE enabled=1' if enabled_only else '')+' ORDER BY display_order,created_at';rows=[dict(x) for x in c.execute(q)]
   for r in rows:r['options']=[dict(x) for x in c.execute('SELECT * FROM human_analysis_field_option WHERE field_id=? ORDER BY display_order',(r['field_id'],))]
   return rows
 def create_field(self,field_key,field_name,field_type,options=None,description='',required=False,enabled=True,display_order=0,default_value=None,validation_rule=None):
  if field_type not in VALID_TYPES:raise ValueError('INVALID_FIELD_TYPE')
  fid='HAF-'+uuid.uuid4().hex;ts=now()
  with self.connect() as c:
   c.execute('INSERT INTO human_analysis_field_definition VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(fid,field_key,field_name,description,field_type,int(required),int(enabled),display_order,default_value,validation_rule,1,ts,ts))
   for i,o in enumerate(options or []):
    val=o.get('value') if isinstance(o,dict) else str(o);label=o.get('label',val) if isinstance(o,dict) else val
    c.execute('INSERT INTO human_analysis_field_option VALUES(?,?,?,?,?,1)',('HAO-'+uuid.uuid4().hex,fid,val,label,i))
  return self.get_field(fid)
 def get_field(self,fid):return next((x for x in self.list_fields() if x['field_id']==fid),None)
 def update_field(self,fid,**changes):
  allowed={'field_name','description','required','enabled','display_order','default_value','validation_rule'};sets=[];vals=[]
  for k,v in changes.items():
   if k in allowed:sets.append(k+'=?');vals.append(int(v) if k in {'required','enabled'} else v)
  if sets:
   vals += [now(),fid]
   with self.connect() as c:c.execute('UPDATE human_analysis_field_definition SET '+','.join(sets)+',config_version=config_version+1,updated_at=? WHERE field_id=?',vals)
  return self.get_field(fid)


 def delete_field(self,field_id):
  field=self.get_field(field_id)
  if not field: raise KeyError(field_id)
  if field['enabled']: raise ValueError('FIELD_MUST_BE_DISABLED_BEFORE_DELETE')
  with self.connect() as c:
   analysis_ids=[x[0] for x in c.execute('SELECT DISTINCT analysis_id FROM human_analysis_value WHERE field_id=?',(field_id,))]
   c.execute('DELETE FROM human_analysis_audit WHERE field_id=?',(field_id,))
   c.execute('DELETE FROM human_analysis_value WHERE field_id=?',(field_id,))
   c.execute('DELETE FROM human_analysis_field_option WHERE field_id=?',(field_id,))
   c.execute('DELETE FROM human_analysis_field_definition WHERE field_id=?',(field_id,))
   for aid in analysis_ids:
    remains=c.execute('SELECT 1 FROM human_analysis_value WHERE analysis_id=? LIMIT 1',(aid,)).fetchone()
    if not remains:
     c.execute('DELETE FROM human_analysis WHERE analysis_id=?',(aid,))
  return {'field_id':field_id,'deleted':True}

 def create_option(self,field_id,option_value,option_label=None,display_order=0,enabled=True):
  field=self.get_field(field_id)
  if not field: raise KeyError(field_id)
  if field['field_type'] not in {'SINGLE_SELECT','MULTI_SELECT'}: raise ValueError('OPTIONS_NOT_SUPPORTED')
  option_value=str(option_value or '').strip()
  if not option_value: raise ValueError('OPTION_VALUE_REQUIRED')
  option_label=str(option_label or option_value).strip()
  with self.connect() as c:
   exists=c.execute('SELECT 1 FROM human_analysis_field_option WHERE field_id=? AND option_value=?',(field_id,option_value)).fetchone()
   if exists: raise ValueError('OPTION_VALUE_DUPLICATE')
   oid='HAO-'+uuid.uuid4().hex
   c.execute('INSERT INTO human_analysis_field_option VALUES(?,?,?,?,?,?)',(oid,field_id,option_value,option_label,int(display_order),int(enabled)))
   c.execute('UPDATE human_analysis_field_definition SET config_version=config_version+1,updated_at=? WHERE field_id=?',(now(),field_id))
  return self.get_option(oid)
 def get_option(self,option_id):
  with self.connect() as c:
   r=c.execute('SELECT * FROM human_analysis_field_option WHERE option_id=?',(option_id,)).fetchone()
   return dict(r) if r else None
 def update_option(self,option_id,option_label=None,display_order=None,enabled=None):
  old=self.get_option(option_id)
  if not old: raise KeyError(option_id)
  sets=[];vals=[]
  if option_label is not None: sets.append('option_label=?');vals.append(str(option_label).strip())
  if display_order is not None: sets.append('display_order=?');vals.append(int(display_order))
  if enabled is not None: sets.append('enabled=?');vals.append(int(enabled))
  if sets:
   with self.connect() as c:
    vals.append(option_id)
    c.execute('UPDATE human_analysis_field_option SET '+','.join(sets)+' WHERE option_id=?',vals)
    c.execute('UPDATE human_analysis_field_definition SET config_version=config_version+1,updated_at=? WHERE field_id=?',(now(),old['field_id']))
  return self.get_option(option_id)
 def option_is_used(self,field_id,option_value):
  needle=json.dumps(option_value,ensure_ascii=False)
  with self.connect() as c:
   r=c.execute('SELECT 1 FROM human_analysis_value WHERE field_id=? AND (value_json=? OR value_json LIKE ?) LIMIT 1',(field_id,needle,'%'+needle+'%')).fetchone()
   return bool(r)

 def save_values(self,knowledge_id,issue_version_id,values,changed_by='web'):
  ts=now()
  with self.connect() as c:
   row=c.execute('SELECT * FROM human_analysis WHERE knowledge_id=? AND issue_version_id=?',(knowledge_id,issue_version_id)).fetchone()
   if row:aid=row['analysis_id'];c.execute('UPDATE human_analysis SET updated_at=? WHERE analysis_id=?',(ts,aid))
   else:aid='HA-'+uuid.uuid4().hex;c.execute('INSERT INTO human_analysis VALUES(?,?,?,?,?,?)',(aid,knowledge_id,issue_version_id,changed_by,ts,ts))
   for fid,val in values.items():
    old=c.execute('SELECT value_json FROM human_analysis_value WHERE analysis_id=? AND field_id=?',(aid,fid)).fetchone();new=json.dumps(val,ensure_ascii=False)
    if old and old['value_json']==new:continue
    c.execute('INSERT INTO human_analysis_value VALUES(?,?,?,?,?) ON CONFLICT(analysis_id,field_id) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at',(aid,fid,new,ts,ts))
    c.execute('INSERT INTO human_analysis_audit VALUES(?,?,?,?,?,?,?,?)',('HAA-'+uuid.uuid4().hex,aid,fid,old['value_json'] if old else None,new,changed_by,ts,'UPDATE' if old else 'CREATE'))
  return self.get_analysis(knowledge_id,issue_version_id)
 def get_analysis(self,knowledge_id,issue_version_id):
  with self.connect() as c:
   a=c.execute('SELECT * FROM human_analysis WHERE knowledge_id=? AND issue_version_id=?',(knowledge_id,issue_version_id)).fetchone()
   if not a:return None
   d=dict(a);d['values']={}
   for x in c.execute('SELECT * FROM human_analysis_value WHERE analysis_id=?',(d['analysis_id'],)):d['values'][x['field_id']]=json.loads(x['value_json']) if x['value_json'] else None
   return d

 def query_analyses(self,field_id=None,value=None,has_analysis=None):
  with self.connect() as c:
   sql='SELECT DISTINCT h.* FROM human_analysis h';args=[];where=[]
   if field_id is not None or value is not None:
    sql+=' JOIN human_analysis_value v ON v.analysis_id=h.analysis_id'
   if field_id is not None:where.append('v.field_id=?');args.append(field_id)
   if value is not None:
    where.append('v.value_json LIKE ?');args.append('%'+json.dumps(value,ensure_ascii=False).strip('"')+'%')
   if where:sql+=' WHERE '+' AND '.join(where)
   rows=[dict(x) for x in c.execute(sql,args)]
   return rows
 def export_rows(self,issue_keys=None):
  fields=self.list_fields(False); byid={f['field_id']:f for f in fields}; out=[]
  with self.connect() as c:
   sql='SELECT * FROM human_analysis'
   args=[]
   if issue_keys:
    marks=','.join('?'*len(issue_keys));sql+=' WHERE knowledge_id IN ('+marks+')';args=list(issue_keys)
   for a in c.execute(sql,args):
    row={'knowledge_id':a['knowledge_id'],'issue_version_id':a['issue_version_id'],'human_analysis_updated_at':a['updated_at']}
    for v in c.execute('SELECT * FROM human_analysis_value WHERE analysis_id=?',(a['analysis_id'],)):
     f=byid.get(v['field_id']); 
     if not f:continue
     val=json.loads(v['value_json']) if v['value_json'] else None
     row['人工分析｜'+f['field_name']]=val
    out.append(row)
  return out

 def list_audit(self,analysis_id):
  with self.connect() as c:return [dict(x) for x in c.execute('SELECT * FROM human_analysis_audit WHERE analysis_id=? ORDER BY changed_at DESC',(analysis_id,))]
