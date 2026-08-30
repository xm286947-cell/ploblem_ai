from __future__ import annotations
import hashlib, json, sqlite3, uuid
from pathlib import Path
from typing import Any
from quality_knowledge.issue_period import normalize_month, normalize_year, parse_itr_period

SCHEMA='''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS quality_issue(
 knowledge_id TEXT PRIMARY KEY,business_type TEXT NOT NULL,business_issue_id TEXT NOT NULL,
 current_version_id TEXT,status TEXT NOT NULL DEFAULT 'ACTIVE',created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(business_type,business_issue_id));
CREATE TABLE IF NOT EXISTS quality_issue_version(
 issue_version_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL REFERENCES quality_issue(knowledge_id),version_no INTEGER NOT NULL,
 normalized_source_hash TEXT NOT NULL,mapping_config_id TEXT,mapping_config_version INTEGER,title TEXT,description TEXT,impact TEXT,severity TEXT,issue_type TEXT,is_defect TEXT,industry TEXT,customer TEXT,month TEXT,
 product TEXT,platform TEXT,department TEXT,business_group TEXT,issue_domain TEXT DEFAULT 'AUTO',issue_domain_source TEXT DEFAULT 'AI',normalized_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(knowledge_id,version_no),UNIQUE(knowledge_id,normalized_source_hash));
CREATE TABLE IF NOT EXISTS issue_source_raw_v1(
 id INTEGER PRIMARY KEY AUTOINCREMENT,issue_version_id TEXT NOT NULL REFERENCES quality_issue_version(issue_version_id),source_file TEXT,source_sheet TEXT,source_row INTEGER,
 raw_json TEXT NOT NULL,source_hash TEXT NOT NULL,import_batch_id TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS issue_product_context_v1(issue_version_id TEXT PRIMARY KEY REFERENCES quality_issue_version(issue_version_id),data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue_occurrence_v1(issue_version_id TEXT PRIMARY KEY REFERENCES quality_issue_version(issue_version_id),data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue_escape_v1(issue_version_id TEXT PRIMARY KEY REFERENCES quality_issue_version(issue_version_id),data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue_solution_v1(issue_version_id TEXT PRIMARY KEY REFERENCES quality_issue_version(issue_version_id),data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue_verification_v1(issue_version_id TEXT PRIMARY KEY REFERENCES quality_issue_version(issue_version_id),data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS issue_product_extension_v1(issue_version_id TEXT PRIMARY KEY REFERENCES quality_issue_version(issue_version_id),data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_batch_v1(
 batch_id TEXT PRIMARY KEY,source_file TEXT,business_type TEXT,status TEXT NOT NULL DEFAULT 'RUNNING',total_rows INTEGER DEFAULT 0,new_rows INTEGER DEFAULT 0,
 updated_rows INTEGER DEFAULT 0,skipped_rows INTEGER DEFAULT 0,failed_rows INTEGER DEFAULT 0,diagnostics_json TEXT DEFAULT '{}',started_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
CREATE TABLE IF NOT EXISTS import_error(
 id INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT NOT NULL REFERENCES import_batch_v1(batch_id),source_file TEXT,source_sheet TEXT,source_row INTEGER,business_type TEXT,
 business_issue_id TEXT,error_code TEXT,error_message TEXT,raw_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_qi_business ON quality_issue(business_type,business_issue_id);
CREATE INDEX IF NOT EXISTS idx_qiv_current ON quality_issue_version(knowledge_id,version_no);
CREATE TABLE IF NOT EXISTS analysis_run(analysis_run_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,analysis_type TEXT NOT NULL,model_provider TEXT,model_name TEXT,prompt_name TEXT,prompt_version TEXT,schema_version TEXT,engine_version TEXT,analysis_profile_json TEXT,status TEXT NOT NULL DEFAULT 'PENDING',started_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT,input_hash TEXT,error_message TEXT);
CREATE TABLE IF NOT EXISTS issue_ai_analysis(id INTEGER PRIMARY KEY AUTOINCREMENT,analysis_run_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,analysis_type TEXT NOT NULL,result_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS analysis_run_debug(analysis_run_id TEXT PRIMARY KEY,analysis_type TEXT NOT NULL,raw_response TEXT,parsed_json TEXT,normalized_json TEXT,validation_error TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS issue_capability_gap(gap_id TEXT PRIMARY KEY,analysis_run_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,dimension TEXT NOT NULL,category TEXT NOT NULL,description TEXT NOT NULL,why_needed TEXT,related_mechanism TEXT,recommended_control TEXT,recommended_action TEXT,action_type TEXT,action_target TEXT,expected_prevention_effect TEXT,scope TEXT,affected_products_json TEXT,priority TEXT,first_action TEXT,verification_metric TEXT,confidence REAL,evidence_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_v1_analysis_issue ON analysis_run(knowledge_id,issue_version_id,analysis_type,started_at);
CREATE INDEX IF NOT EXISTS idx_v1_gap ON issue_capability_gap(knowledge_id,dimension,category);
CREATE TABLE IF NOT EXISTS issue_domain_audit(audit_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,old_domain TEXT,new_domain TEXT,source TEXT NOT NULL,changed_by TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS issue_period_audit(audit_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,old_year TEXT,old_month TEXT,new_year TEXT,new_month TEXT,changed_by TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS analysis_open_question(
 question_id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL,issue_version_id TEXT NOT NULL,analysis_run_id TEXT NOT NULL,stage TEXT NOT NULL,question_key TEXT,
 question_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',answer TEXT,evidence TEXT,confirmed_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_open_question_issue ON analysis_open_question(knowledge_id,issue_version_id,status);
'''

def _dump(x): return json.dumps(x,ensure_ascii=False,default=str)

class IssueKnowledgeRepository:
    def __init__(self,db_path:str|Path):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)
            cols={r['name'] for r in c.execute('PRAGMA table_info(issue_capability_gap)').fetchall()}
            for name in ['recommended_action','action_type','action_target','expected_prevention_effect','priority','first_action','verification_metric']:
                if name not in cols: c.execute(f'ALTER TABLE issue_capability_gap ADD COLUMN {name} TEXT')
            vcols={r['name'] for r in c.execute('PRAGMA table_info(quality_issue_version)').fetchall()}
            if 'mapping_config_id' not in vcols: c.execute('ALTER TABLE quality_issue_version ADD COLUMN mapping_config_id TEXT')
            if 'mapping_config_version' not in vcols: c.execute('ALTER TABLE quality_issue_version ADD COLUMN mapping_config_version INTEGER')
            if 'issue_domain' not in vcols: c.execute("ALTER TABLE quality_issue_version ADD COLUMN issue_domain TEXT DEFAULT 'AUTO'")
            if 'issue_domain_source' not in vcols: c.execute("ALTER TABLE quality_issue_version ADD COLUMN issue_domain_source TEXT DEFAULT 'AI'")
            if 'year' not in vcols: c.execute("ALTER TABLE quality_issue_version ADD COLUMN year TEXT DEFAULT ''")
            if 'year_source' not in vcols: c.execute("ALTER TABLE quality_issue_version ADD COLUMN year_source TEXT DEFAULT 'ISSUE_ID'")
            if 'month_source' not in vcols: c.execute("ALTER TABLE quality_issue_version ADD COLUMN month_source TEXT DEFAULT 'SOURCE_DATA'")
            rows=c.execute("SELECT v.issue_version_id,q.business_issue_id,v.year,v.month FROM quality_issue_version v JOIN quality_issue q ON q.knowledge_id=v.knowledge_id").fetchall()
            for row in rows:
                parsed_year,parsed_month=parse_itr_period(row['business_issue_id'])
                if not str(row['year'] or '').strip() and parsed_year:
                    c.execute("UPDATE quality_issue_version SET year=?,year_source='ISSUE_ID' WHERE issue_version_id=?",(parsed_year,row['issue_version_id']))
                if not str(row['month'] or '').strip() and parsed_month:
                    c.execute("UPDATE quality_issue_version SET month=?,month_source='ISSUE_ID' WHERE issue_version_id=?",(parsed_month,row['issue_version_id']))
            acols={r['name'] for r in c.execute('PRAGMA table_info(analysis_run)').fetchall()}
            if 'analysis_profile_json' not in acols: c.execute('ALTER TABLE analysis_run ADD COLUMN analysis_profile_json TEXT')
    def connect(self):
        c=sqlite3.connect(self.db_path); c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
    def begin_import(self,batch_id,source_file,business_type,diagnostics=None):
        with self.connect() as c:c.execute('INSERT INTO import_batch_v1(batch_id,source_file,business_type,diagnostics_json) VALUES(?,?,?,?)',(batch_id,source_file,business_type or 'AUTO',_dump(diagnostics or {})))
    def finish_import(self,batch_id,stats,status='COMPLETED',diagnostics=None):
        with self.connect() as c:c.execute('''UPDATE import_batch_v1 SET status=?,total_rows=?,new_rows=?,updated_rows=?,skipped_rows=?,failed_rows=?,diagnostics_json=?,completed_at=CURRENT_TIMESTAMP WHERE batch_id=?''',(status,stats['total'],stats['new'],stats['updated'],stats['skipped'],stats['failed'],_dump(diagnostics or {}),batch_id))
    def add_import_error(self,batch_id,*,source_file='',source_sheet='',source_row=None,business_type='',business_issue_id='',error_code='ROW_IMPORT_FAILED',error_message='',raw=None):
        with self.connect() as c:c.execute('''INSERT INTO import_error(batch_id,source_file,source_sheet,source_row,business_type,business_issue_id,error_code,error_message,raw_json) VALUES(?,?,?,?,?,?,?,?,?)''',(batch_id,source_file,source_sheet,source_row,business_type,business_issue_id,error_code,error_message,_dump(raw or {})))
    def get_by_business_key(self,business_type,business_issue_id):
        with self.connect() as c:
            r=c.execute('SELECT * FROM quality_issue WHERE business_type=? AND business_issue_id=?',(business_type,business_issue_id)).fetchone();return dict(r) if r else None
    def upsert_source_record(self,q,normalized_hash,*,mapping_config_id=None,mapping_config_version=None):
        bt=q.identity.business_type; bid=q.identity.issue_id
        existing=self.get_by_business_key(bt,bid)
        with self.connect() as c:
            if existing:
                current=c.execute('SELECT * FROM quality_issue_version WHERE issue_version_id=?',(existing['current_version_id'],)).fetchone()
                if current and current['normalized_source_hash']==normalized_hash:return {'action':'SKIPPED','knowledge_id':existing['knowledge_id'],'issue_version_id':current['issue_version_id']}
                knowledge_id=existing['knowledge_id']; version_no=c.execute('SELECT COALESCE(MAX(version_no),0)+1 FROM quality_issue_version WHERE knowledge_id=?',(knowledge_id,)).fetchone()[0]; action='UPDATED'
            else:
                knowledge_id=f"QK-{bt}-{uuid.uuid5(uuid.NAMESPACE_URL,bt+'|'+bid).hex[:16]}"; version_no=1; action='NEW'
                c.execute('INSERT INTO quality_issue(knowledge_id,business_type,business_issue_id) VALUES(?,?,?)',(knowledge_id,bt,bid))
            vid=f'{knowledge_id}-V{version_no}'
            f=q.issue_fact
            parsed_year,parsed_month=parse_itr_period(bid)
            f.year=parsed_year
            month_from_source=bool(str(f.month or '').strip())
            if not month_from_source and parsed_month: f.month=parsed_month
            normalized={'issue_fact':f.model_dump(),'product_context':q.product_context.model_dump(),'occurrence':q.occurrence.model_dump(),'escape':q.escape.model_dump(),'solution':q.solution.model_dump(),'verification':q.verification.model_dump(),'product_extension':q.product_extension}
            c.execute('''INSERT INTO quality_issue_version(issue_version_id,knowledge_id,version_no,normalized_source_hash,mapping_config_id,mapping_config_version,title,description,impact,severity,issue_type,is_defect,industry,customer,month,year,year_source,month_source,product,platform,department,business_group,issue_domain,issue_domain_source,normalized_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(vid,knowledge_id,version_no,normalized_hash,mapping_config_id,mapping_config_version,f.title,f.description,f.impact,f.severity,f.issue_type,f.is_defect,f.industry,f.customer,f.month,f.year,'ISSUE_ID','SOURCE_DATA' if month_from_source else 'ISSUE_ID',f.product,f.platform,f.department,f.business_group,f.issue_domain,f.issue_domain_source,_dump(normalized)))
            c.execute('INSERT INTO issue_source_raw_v1(issue_version_id,source_file,source_sheet,source_row,raw_json,source_hash,import_batch_id) VALUES(?,?,?,?,?,?,?)',(vid,q.source.source_file,q.source.source_sheet,q.source.source_row,_dump(q.raw_record),q.source.source_hash,q.source.source_import_batch))
            for table,obj in [('issue_product_context_v1',q.product_context.model_dump()),('issue_occurrence_v1',q.occurrence.model_dump()),('issue_escape_v1',q.escape.model_dump()),('issue_solution_v1',q.solution.model_dump()),('issue_verification_v1',q.verification.model_dump()),('issue_product_extension_v1',q.product_extension)]:
                c.execute(f'INSERT INTO {table}(issue_version_id,data_json) VALUES(?,?)',(vid,_dump(obj)))
            c.execute('UPDATE quality_issue SET current_version_id=?,updated_at=CURRENT_TIMESTAMP WHERE knowledge_id=?',(vid,knowledge_id))
            return {'action':action,'knowledge_id':knowledge_id,'issue_version_id':vid}
    def get_current_issue(self,knowledge_id):
        with self.connect() as c:
            r=c.execute('''SELECT q.*,v.* FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id WHERE q.knowledge_id=?''',(knowledge_id,)).fetchone();return dict(r) if r else None
    def get_issue_history(self,knowledge_id):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM quality_issue_version WHERE knowledge_id=? ORDER BY version_no DESC',(knowledge_id,)).fetchall()]
    @staticmethod
    def _current_issue_filter_sql(filters=None):
        filters=dict(filters or {})
        allowed={'business_type':'q.business_type','business_issue_id':'q.business_issue_id','product':'v.product','platform':'v.platform','severity':'v.severity','issue_type':'v.issue_type','issue_domain':'v.issue_domain','month':'v.month','year':'v.year'}
        where=[]; vals=[]
        for key,column in (('business_issue_ids','q.business_issue_id'),('knowledge_ids','q.knowledge_id')):
            values=filters.pop(key,None)
            if values:
                if isinstance(values,str): values=values.split(',')
                values=[str(x).strip() for x in values if str(x).strip()]
                if values:
                    where.append(column+' IN ('+','.join('?' for _ in values)+')'); vals.extend(values)
        gap_dimension=str(filters.pop('gap_dimension','') or '').upper()
        gap_category=str(filters.pop('gap_category','') or '').strip()
        if gap_dimension or gap_category:
            gap_where=['g.knowledge_id=q.knowledge_id','g.issue_version_id=q.current_version_id',"r.status='COMPLETED'",'r.analysis_run_id=g.analysis_run_id']
            if gap_dimension: gap_where.append('g.dimension=?'); vals.append(gap_dimension)
            if gap_category: gap_where.append('g.category=?'); vals.append(gap_category)
            where.append('EXISTS (SELECT 1 FROM issue_capability_gap g JOIN analysis_run r ON r.analysis_run_id=g.analysis_run_id WHERE '+' AND '.join(gap_where)+')')
        for key,value in filters.items():
            if key not in allowed: raise ValueError(f'Unsupported filter: {key}')
            where.append(f'{allowed[key]}=?'); vals.append(value)
        return where,vals

    def query_current_issues(self,filters=None,limit=100,offset=0):
        where,vals=self._current_issue_filter_sql(filters)
        sql='''SELECT q.knowledge_id,q.business_type,q.business_issue_id,q.current_version_id,q.status,v.version_no,v.title,v.description,v.product,v.platform,v.severity,v.issue_type,v.issue_domain,v.issue_domain_source,v.year,v.year_source,v.month,v.month_source,q.updated_at FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id'''
        if where: sql+=' WHERE '+' AND '.join(where)
        sql+=' ORDER BY q.updated_at DESC,q.knowledge_id LIMIT ? OFFSET ?'; vals.extend([max(1,int(limit)),max(0,int(offset))])
        with self.connect() as c:return [dict(r) for r in c.execute(sql,vals).fetchall()]

    def count_current_issues(self,filters=None):
        where,vals=self._current_issue_filter_sql(filters)
        sql='''SELECT COUNT(*) FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id'''
        if where: sql+=' WHERE '+' AND '.join(where)
        with self.connect() as c:return int(c.execute(sql,vals).fetchone()[0])

    def get_issue_detail(self,knowledge_id):
        issue=self.get_current_issue(knowledge_id)
        if not issue:return None
        with self.connect() as c:
            raw=c.execute('SELECT * FROM issue_source_raw_v1 WHERE issue_version_id=? ORDER BY id DESC LIMIT 1',(issue['current_version_id'],)).fetchone()
        return {'issue':issue,'raw':dict(raw) if raw else None,'history':self.get_issue_history(knowledge_id)}

    def update_issue_period(self,knowledge_id,year,month,changed_by='web'):
        year=normalize_year(year); month=normalize_month(month)
        with self.connect() as c:
            row=c.execute('''SELECT q.current_version_id,v.year,v.month,v.normalized_json FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id WHERE q.knowledge_id=?''',(knowledge_id,)).fetchone()
            if not row: raise KeyError(knowledge_id)
            normalized=json.loads(row['normalized_json'] or '{}')
            fact=normalized.setdefault('issue_fact',{})
            fact['year']=year; fact['month']=month
            c.execute("UPDATE quality_issue_version SET year=?,month=?,year_source='HUMAN_OVERRIDE',month_source='HUMAN_OVERRIDE',normalized_json=? WHERE issue_version_id=?",(year,month,_dump(normalized),row['current_version_id']))
            c.execute('''INSERT INTO issue_period_audit(audit_id,knowledge_id,issue_version_id,old_year,old_month,new_year,new_month,changed_by) VALUES(?,?,?,?,?,?,?,?)''',('IPA-'+uuid.uuid4().hex,knowledge_id,row['current_version_id'],row['year'],row['month'],year,month,changed_by))
            c.execute('UPDATE quality_issue SET updated_at=CURRENT_TIMESTAMP WHERE knowledge_id=?',(knowledge_id,))
        return self.get_current_issue(knowledge_id)
    def get_import_batch(self,batch_id):
        with self.connect() as c:
            b=c.execute('SELECT * FROM import_batch_v1 WHERE batch_id=?',(batch_id,)).fetchone(); errs=c.execute('SELECT * FROM import_error WHERE batch_id=? ORDER BY id',(batch_id,)).fetchall();return {'batch':dict(b) if b else None,'errors':[dict(x) for x in errs]}
    def list_import_batches(self,limit=50):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM import_batch_v1 ORDER BY started_at DESC LIMIT ?',(limit,)).fetchall()]

# M3 AI Analysis repository methods
def _v1_get_analysis_context(self, knowledge_id):
    d=self.get_issue_detail(knowledge_id)
    if not d:return None
    issue=d['issue']; normalized=json.loads(issue.get('normalized_json') or '{}')
    return {'knowledge_id':knowledge_id,'issue_version_id':issue['current_version_id'],'business_type':issue['business_type'],'business_issue_id':issue['business_issue_id'],**normalized}
IssueKnowledgeRepository.get_analysis_context=_v1_get_analysis_context

def _v1_start_run(self,run):
    with self.connect() as c:c.execute("INSERT INTO analysis_run(analysis_run_id,knowledge_id,issue_version_id,analysis_type,model_provider,prompt_name,prompt_version,schema_version,engine_version,analysis_profile_json,status,input_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(run['analysis_run_id'],run['knowledge_id'],run['issue_version_id'],run['analysis_type'],run.get('model_provider'),run.get('prompt_name'),run.get('prompt_version'),run.get('schema_version'),run.get('engine_version'),_dump(run.get('analysis_profile') or {}),run.get('status','RUNNING'),run.get('input_hash')))
IssueKnowledgeRepository.start_analysis_run=_v1_start_run

def _v1_finish_run(self,run_id,status,model_name=None,error_message=None):
    with self.connect() as c:c.execute('UPDATE analysis_run SET status=?,model_name=COALESCE(?,model_name),error_message=?,completed_at=CURRENT_TIMESTAMP WHERE analysis_run_id=?',(status,model_name,error_message,run_id))
IssueKnowledgeRepository.finish_analysis_run=_v1_finish_run

def _v1_save_result(self,kid,vid,rid,atype,result):
    with self.connect() as c:
        c.execute('INSERT INTO issue_ai_analysis(analysis_run_id,knowledge_id,issue_version_id,analysis_type,result_json) VALUES(?,?,?,?,?)',(rid,kid,vid,atype,_dump(result)))
        questions=result.get('open_questions',[]) if isinstance(result,dict) else []
        for q in questions[:3]:
            if not isinstance(q,dict) or not q.get('question'): continue
            key=str(q.get('question_key') or q.get('question'))
            qid='AIQ-'+hashlib.sha256(f'{kid}|{vid}|{atype}|{key}|{q.get("question")}'.encode()).hexdigest()[:24]
            c.execute('''INSERT INTO analysis_open_question(question_id,knowledge_id,issue_version_id,analysis_run_id,stage,question_key,question_json)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET analysis_run_id=excluded.analysis_run_id,question_json=excluded.question_json,updated_at=CURRENT_TIMESTAMP''',(qid,kid,vid,rid,atype,key,_dump(q)))
IssueKnowledgeRepository.save_analysis_result=_v1_save_result

def _v1_list_open_questions(self,kid,limit=5):
    with self.connect() as c:
        rows=c.execute('''SELECT oq.* FROM analysis_open_question oq JOIN quality_issue q ON q.knowledge_id=oq.knowledge_id
          WHERE oq.knowledge_id=? AND oq.issue_version_id=q.current_version_id
          ORDER BY CASE json_extract(oq.question_json,'$.priority') WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,oq.created_at LIMIT ?''',(kid,int(limit))).fetchall()
        out=[]
        for row in rows:
            d=dict(row); d['question']=json.loads(d.pop('question_json') or '{}'); out.append(d)
        return out
IssueKnowledgeRepository.list_open_questions=_v1_list_open_questions

def _v1_save_question_confirmations(self,kid,answers,confirmed_by='web'):
    allowed={'CONFIRMED','CORRECTED','UNRESOLVED','NOT_APPLICABLE'};updated=0
    with self.connect() as c:
        for qid,value in (answers or {}).items():
            row=c.execute('SELECT * FROM analysis_open_question WHERE question_id=? AND knowledge_id=?',(qid,kid)).fetchone()
            if not row: continue
            status=str(value.get('status') or 'UNRESOLVED').upper()
            if status not in allowed: status='UNRESOLVED'
            c.execute('UPDATE analysis_open_question SET status=?,answer=?,evidence=?,confirmed_by=?,updated_at=CURRENT_TIMESTAMP WHERE question_id=?',(status,str(value.get('answer') or ''),str(value.get('evidence') or ''),confirmed_by,qid));updated+=1
    return updated
IssueKnowledgeRepository.save_question_confirmations=_v1_save_question_confirmations

def _v1_human_confirmations(self,kid):
    return [{'question_id':x['question_id'],'stage':x['stage'],'question':x['question'].get('question'),'status':x['status'],'answer':x.get('answer') or '','evidence':x.get('evidence') or '','confirmed_by':x.get('confirmed_by')} for x in self.list_open_questions(kid,50) if x.get('status')!='PENDING']
IssueKnowledgeRepository.get_human_confirmations=_v1_human_confirmations

def _v1_save_gaps(self,kid,vid,rid,gaps):
    with self.connect() as c:
        for g in gaps:
            d=g.model_dump(mode='json') if hasattr(g,'model_dump') else g
            c.execute('INSERT INTO issue_capability_gap(gap_id,analysis_run_id,knowledge_id,issue_version_id,dimension,category,description,why_needed,related_mechanism,recommended_control,recommended_action,action_type,action_target,expected_prevention_effect,scope,affected_products_json,priority,first_action,verification_metric,confidence,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('GAP-'+uuid.uuid4().hex,rid,kid,vid,d.get('dimension') or d.get('gap_dimension'),d.get('category') or d.get('gap_category'),d.get('description') or d.get('gap_description'),d.get('why_needed'),d.get('related_mechanism') or d.get('related_issue_mechanism'),d.get('recommended_control') or d.get('recommended_action'),d.get('recommended_action') or d.get('recommended_control'),d.get('action_type'),d.get('action_target'),d.get('expected_prevention_effect'),d.get('scope'),_dump(d.get('affected_products') or []),d.get('priority') or 'P2',d.get('first_action'),d.get('verification_metric'),d.get('confidence'),_dump(d.get('evidence') or d.get('evidence_refs') or [])))
IssueKnowledgeRepository.replace_capability_gaps=_v1_save_gaps

def _v1_latest(self,kid,atype):
    with self.connect() as c:
        r=c.execute("SELECT a.result_json,r.* FROM issue_ai_analysis a JOIN analysis_run r USING(analysis_run_id) JOIN quality_issue q ON q.knowledge_id=a.knowledge_id WHERE a.knowledge_id=? AND a.analysis_type=? AND a.issue_version_id=q.current_version_id AND r.status='COMPLETED' ORDER BY a.id DESC LIMIT 1",(kid,atype)).fetchone()
        if not r:return None
        d=dict(r);d['result']=json.loads(d.pop('result_json'));return d
IssueKnowledgeRepository.get_latest_analysis=_v1_latest

def _v1_history(self,kid):
    with self.connect() as c: rows=[dict(r) for r in c.execute('SELECT * FROM analysis_run WHERE knowledge_id=? ORDER BY started_at DESC',(kid,)).fetchall()]
    for row in rows:
        try: row['analysis_agent']=(json.loads(row.get('analysis_profile_json') or '{}').get('analysis_agent') or 'DEFAULT')
        except (TypeError,ValueError): row['analysis_agent']='DEFAULT'
    return rows
IssueKnowledgeRepository.get_analysis_history=_v1_history

def _v1_run(self,rid):
    with self.connect() as c:
        r=c.execute('SELECT * FROM analysis_run WHERE analysis_run_id=?',(rid,)).fetchone();return dict(r) if r else None
IssueKnowledgeRepository.get_analysis_run=_v1_run

def _v1_set_issue_domain(self, knowledge_ids, domain, source='USER', changed_by='web'):
    domain = str(domain or 'AUTO').upper(); updated = 0
    with self.connect() as c:
        for kid in knowledge_ids:
            row = c.execute('SELECT q.current_version_id,v.issue_domain,v.normalized_json FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id WHERE q.knowledge_id=?',(kid,)).fetchone()
            if not row: continue
            normalized = json.loads(row['normalized_json'] or '{}')
            for section in ('issue_fact','fact'):
                if isinstance(normalized.get(section), dict):
                    normalized[section]['issue_domain']=domain; normalized[section]['issue_domain_source']=source
            c.execute('UPDATE quality_issue_version SET issue_domain=?,issue_domain_source=?,normalized_json=?,updated_at=CURRENT_TIMESTAMP WHERE issue_version_id=?',(domain,source,_dump(normalized),row['current_version_id']))
            c.execute('INSERT INTO issue_domain_audit(audit_id,knowledge_id,old_domain,new_domain,source,changed_by) VALUES(?,?,?,?,?,?)',(f'DOMAIN-{uuid.uuid4().hex}',kid,row['issue_domain'] or 'AUTO',domain,source,changed_by))
            updated += 1
    return updated
IssueKnowledgeRepository.set_issue_domain = _v1_set_issue_domain


def _v1_expire_stale_runs(self, stage_timeouts, buffer_seconds=60):
    expired=[]
    with self.connect() as c:
        for stage,timeout in (stage_timeouts or {}).items():
            cutoff=max(1,int(timeout)+int(buffer_seconds))
            rows=c.execute(
                "SELECT analysis_run_id FROM analysis_run WHERE status='RUNNING' AND analysis_type=? AND datetime(started_at) < datetime('now', ?)",
                (stage, f'-{cutoff} seconds')
            ).fetchall()
            for row in rows:
                rid=row['analysis_run_id']
                c.execute(
                    "UPDATE analysis_run SET status='FAILED',error_message=?,completed_at=CURRENT_TIMESTAMP WHERE analysis_run_id=? AND status='RUNNING'",
                    (f'STALE_RUNNING_TIMEOUT: stage={stage}, timeout={timeout}s, buffer={buffer_seconds}s',rid)
                )
                expired.append(rid)
    return expired
IssueKnowledgeRepository.expire_stale_analysis_runs=_v1_expire_stale_runs


def _v1_gaps(self,kid=None):
    sql='SELECT * FROM issue_capability_gap';vals=[]
    if kid:sql+=' WHERE knowledge_id=?';vals=[kid]
    sql+=' ORDER BY created_at DESC'
    with self.connect() as c:return [dict(r) for r in c.execute(sql,vals).fetchall()]
IssueKnowledgeRepository.list_capability_gaps=_v1_gaps

# M4 Statistics / Export repository methods (Current Version + latest valid analysis only)
def _v1_statistics(self,business_type=None,limit=20,knowledge_ids=None):
    bt_sql=' AND q.business_type=?' if business_type else ''
    vals=[business_type] if business_type else []
    if knowledge_ids is not None:
        knowledge_ids=list(knowledge_ids) or ['__NO_SCOPE_MATCH__']
        bt_sql+=' AND q.knowledge_id IN ('+','.join('?' for _ in knowledge_ids)+')'
        vals.extend(knowledge_ids)
    def rows(sql, params=()):
        with self.connect() as c:return [dict(r) for r in c.execute(sql,params).fetchall()]
    # Quality management requires the original detailed classifications here:
    # occurrence uses cause_l4 and escape uses escape_l3. AI MRC is presented in
    # its own matrix and must not overwrite these source-data rankings.
    occ_value = "NULLIF(json_extract(v.normalized_json,'$.occurrence.cause_l4'),'')"
    esc_value = "NULLIF(json_extract(v.normalized_json,'$.escape.escape_l3'),'')"
    occ=rows(f'''SELECT COALESCE({occ_value},'未分类') category,COUNT(*) count
      FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id
      WHERE 1=1 {bt_sql} AND ({occ_value}) IS NOT NULL AND TRIM(CAST(({occ_value}) AS TEXT))<>''
      GROUP BY category ORDER BY count DESC LIMIT ?''',tuple(vals+[limit]))
    esc=rows(f'''SELECT COALESCE({esc_value},'未分类') category,COUNT(*) count
      FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id
      WHERE 1=1 {bt_sql} AND ({esc_value}) IS NOT NULL AND TRIM(CAST(({esc_value}) AS TEXT))<>''
      GROUP BY category ORDER BY count DESC LIMIT ?''',tuple(vals+[limit]))
    products=rows(f'''SELECT COALESCE(v.product,'未分类') category,COUNT(*) count FROM quality_issue q JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id WHERE 1=1 {bt_sql} GROUP BY category ORDER BY count DESC LIMIT ?''',tuple(vals+[limit]))
    def gaps(dim):
        return rows(f'''SELECT g.category,COUNT(*) count FROM issue_capability_gap g JOIN quality_issue q ON q.knowledge_id=g.knowledge_id
          JOIN analysis_run r ON r.analysis_run_id=g.analysis_run_id
          WHERE g.issue_version_id=q.current_version_id AND r.status='COMPLETED' AND g.dimension=? {bt_sql}
          GROUP BY g.category ORDER BY count DESC LIMIT ?''',tuple([dim]+vals+[limit]))
    common=rows('''SELECT g.category,COUNT(DISTINCT q.business_type) business_count,GROUP_CONCAT(DISTINCT q.business_type) businesses,COUNT(*) count
      FROM issue_capability_gap g JOIN quality_issue q ON q.knowledge_id=g.knowledge_id JOIN analysis_run r ON r.analysis_run_id=g.analysis_run_id
      WHERE g.issue_version_id=q.current_version_id AND r.status='COMPLETED'
      GROUP BY g.category HAVING COUNT(DISTINCT q.business_type)>1 ORDER BY business_count DESC,count DESC LIMIT ?''',(limit,))
    return {'top_occurrence_causes':occ,'top_escape_causes':esc,'product_distribution':products,'top_technical_gaps':gaps('TECHNICAL'),'top_management_gaps':gaps('MANAGEMENT'),'top_governance_gaps':gaps('GOVERNANCE'),'cross_product_common_gaps':common}
IssueKnowledgeRepository.statistics=_v1_statistics

def _v1_export_rows(self,filters=None,limit=1000000):
    items=self.query_current_issues(filters or {},limit)
    out=[]
    for x in items:
        d=self.get_issue_detail(x['knowledge_id']); issue=d['issue']; n=json.loads(issue.get('normalized_json') or '{}')
        rec=self.get_latest_analysis(x['knowledge_id'],'recurrence'); out.append({**x,'normalized':n,'recurrence':rec.get('result') if rec else None})
    return out
IssueKnowledgeRepository.export_current_issues=_v1_export_rows

def _v1_current_gaps(self,filters=None,limit=1000000):
    filters=filters or {}; where=["g.issue_version_id=q.current_version_id","r.status='COMPLETED'"];vals=[]
    amap={'business_type':'q.business_type','dimension':'g.dimension','category':'g.category','knowledge_id':'g.knowledge_id'}
    for k,v in filters.items():
        if k not in amap:continue
        where.append(amap[k]+'=?');vals.append(v)
    vals.append(limit)
    sql='''SELECT g.*,q.business_type,q.business_issue_id,v.title,v.product,v.platform FROM issue_capability_gap g JOIN quality_issue q ON q.knowledge_id=g.knowledge_id JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id JOIN analysis_run r ON r.analysis_run_id=g.analysis_run_id WHERE '''+' AND '.join(where)+' ORDER BY g.created_at DESC LIMIT ?'
    with self.connect() as c:return [dict(r) for r in c.execute(sql,vals).fetchall()]
IssueKnowledgeRepository.query_current_capability_gaps=_v1_current_gaps


def _v1_save_analysis_debug(self,run_id,analysis_type,debug):
    debug=debug or {}
    def dumps(v):
        if v is None:return None
        if isinstance(v,str):return v
        return _dump(v)
    sql = """INSERT INTO analysis_run_debug(analysis_run_id,analysis_type,raw_response,parsed_json,normalized_json,validation_error,updated_at)
             VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
             ON CONFLICT(analysis_run_id) DO UPDATE SET
             analysis_type=excluded.analysis_type,raw_response=excluded.raw_response,
             parsed_json=excluded.parsed_json,normalized_json=excluded.normalized_json,
             validation_error=excluded.validation_error,updated_at=CURRENT_TIMESTAMP"""
    with self.connect() as c:
        c.execute(sql,(run_id,analysis_type,dumps(debug.get('raw_response')),dumps(debug.get('parsed_json')),dumps(debug.get('normalized_json')),debug.get('validation_error')))
IssueKnowledgeRepository.save_analysis_debug=_v1_save_analysis_debug

def _v1_get_analysis_debug(self,run_id):
    with self.connect() as c:
        r=c.execute('SELECT * FROM analysis_run_debug WHERE analysis_run_id=?',(run_id,)).fetchone()
        return dict(r) if r else None
IssueKnowledgeRepository.get_analysis_debug=_v1_get_analysis_debug


def _v1_common_capability_gaps(self, *, business_type=None, dimension=None, min_issues=2, limit=50, knowledge_ids=None):
    where=["g.issue_version_id=q.current_version_id","r.status='COMPLETED'"]; vals=[]
    if dimension: where.append('g.dimension=?'); vals.append(dimension)
    if knowledge_ids is not None:
        knowledge_ids=list(knowledge_ids) or ['__NO_SCOPE_MATCH__']
        where.append('q.knowledge_id IN ('+','.join('?' for _ in knowledge_ids)+')'); vals.extend(knowledge_ids)
    having=['COUNT(DISTINCT g.knowledge_id)>=?']; vals.append(min_issues)
    # Product filtering means “gaps involving this product”.  Coverage breadth is
    # still calculated from the global group so cross-product relationships remain.
    if business_type:
        having.append('SUM(CASE WHEN q.business_type=? THEN 1 ELSE 0 END)>0'); vals.append(business_type)
    vals.append(limit)
    sql=f"""SELECT g.dimension,g.category,COUNT(DISTINCT g.knowledge_id) related_issue_count,
      COUNT(DISTINCT q.business_type) business_type_count,GROUP_CONCAT(DISTINCT q.business_type) business_types,
      GROUP_CONCAT(DISTINCT COALESCE(v.product,'')) products,GROUP_CONCAT(DISTINCT COALESCE(v.platform,'')) platforms,
      GROUP_CONCAT(DISTINCT q.business_issue_id) related_issues,
      GROUP_CONCAT(DISTINCT q.knowledge_id) related_knowledge_ids,
      MAX(COALESCE(g.recommended_action,g.recommended_control,'')) recommended_governance,
      MAX(COALESCE(g.expected_prevention_effect,'')) expected_prevention_effect
      FROM issue_capability_gap g JOIN quality_issue q ON q.knowledge_id=g.knowledge_id
      JOIN quality_issue_version v ON v.issue_version_id=q.current_version_id
      JOIN analysis_run r ON r.analysis_run_id=g.analysis_run_id
      WHERE {' AND '.join(where)} GROUP BY g.dimension,g.category
      HAVING {' AND '.join(having)} ORDER BY related_issue_count DESC,business_type_count DESC LIMIT ?"""
    with self.connect() as c:return [dict(x) for x in c.execute(sql,vals).fetchall()]
IssueKnowledgeRepository.aggregate_common_capability_gaps=_v1_common_capability_gaps


def _v1_workspace_metrics(self, business_type=None, knowledge_ids=None):
    where='WHERE q.business_type=?' if business_type else ''
    params=[business_type] if business_type else []
    if knowledge_ids is not None:
        knowledge_ids=list(knowledge_ids) or ['__NO_SCOPE_MATCH__']
        where+=(' AND ' if where else 'WHERE ')+'q.knowledge_id IN ('+','.join('?' for _ in knowledge_ids)+')'
        params.extend(knowledge_ids)
    with self.connect() as c:
        human_exists=bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='human_analysis'").fetchone())
        human_metric=("SUM(CASE WHEN EXISTS(SELECT 1 FROM human_analysis h WHERE h.knowledge_id=q.knowledge_id AND h.issue_version_id=q.current_version_id) THEN 1 ELSE 0 END)" if human_exists else "0")
        base=c.execute(f'''SELECT COUNT(*) issue_count,
          SUM(CASE WHEN EXISTS(SELECT 1 FROM analysis_run ar WHERE ar.knowledge_id=q.knowledge_id AND ar.issue_version_id=q.current_version_id AND ar.status IN ('COMPLETED','SUCCESS')) THEN 1 ELSE 0 END) analyzed_count,
          {human_metric} human_analyzed_count
          FROM quality_issue q {where}''',params).fetchone()
        risk_rows=c.execute(f'''SELECT UPPER(COALESCE(
            NULLIF(json_extract(a.result_json,'$.recurrence_risk_level'),''),
            NULLIF(json_extract(a.result_json,'$.result.recurrence_risk_level'),''),'UNKNOWN')) category,COUNT(*) count
          FROM quality_issue q
          LEFT JOIN issue_ai_analysis a ON a.id=(SELECT MAX(a2.id) FROM issue_ai_analysis a2
            JOIN analysis_run ar2 ON ar2.analysis_run_id=a2.analysis_run_id
            WHERE a2.knowledge_id=q.knowledge_id AND a2.issue_version_id=q.current_version_id
              AND a2.analysis_type='recurrence' AND ar2.status='COMPLETED')
          {where} GROUP BY category''',params).fetchall()
    result={k:int(base[k] or 0) for k in ('issue_count','analyzed_count','human_analyzed_count')}
    result['risk_distribution']=[{'category':str(x['category'] or 'UNKNOWN').upper(),'count':int(x['count'] or 0)} for x in risk_rows]
    result['high_risk_count']=sum(x['count'] for x in result['risk_distribution'] if x['category']=='HIGH')
    return result
IssueKnowledgeRepository.workspace_metrics=_v1_workspace_metrics
