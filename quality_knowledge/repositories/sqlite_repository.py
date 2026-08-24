from __future__ import annotations
import json, sqlite3
from pathlib import Path
from typing import Any
from quality_knowledge.models.issue import QualityIssueDTO

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS import_batch (id TEXT PRIMARY KEY, business_type TEXT NOT NULL, source_file TEXT, started_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT, status TEXT NOT NULL DEFAULT 'RUNNING', total_rows INTEGER DEFAULT 0, imported_rows INTEGER DEFAULT 0, skipped_rows INTEGER DEFAULT 0, failed_rows INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS issue_record (id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_id TEXT UNIQUE NOT NULL, business_type TEXT NOT NULL, issue_id TEXT NOT NULL, title TEXT, description TEXT, impact TEXT, severity TEXT, issue_type TEXT, is_defect TEXT, industry TEXT, customer TEXT, month TEXT, product TEXT, platform TEXT, department TEXT, business_group TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_issue_business_id ON issue_record(business_type, issue_id);
CREATE INDEX IF NOT EXISTS idx_issue_product ON issue_record(product, platform);
CREATE TABLE IF NOT EXISTS issue_source_raw (id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_id TEXT NOT NULL REFERENCES issue_record(knowledge_id), source_file TEXT, source_sheet TEXT, source_row INTEGER, raw_json TEXT NOT NULL, import_batch_id TEXT, source_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(knowledge_id, source_hash));
CREATE TABLE IF NOT EXISTS issue_product_context (knowledge_id TEXT PRIMARY KEY REFERENCES issue_record(knowledge_id), product TEXT, product_series TEXT, platform TEXT, module TEXT, business_group TEXT, feature_l1 TEXT, feature_l2 TEXT, feature_l3 TEXT, feature_l4 TEXT, fa_feature TEXT, fa_l1_feature TEXT, layer TEXT, layer_category TEXT);
CREATE TABLE IF NOT EXISTS issue_occurrence (knowledge_id TEXT PRIMARY KEY REFERENCES issue_record(knowledge_id), original_reason TEXT, root_cause_original TEXT, cause_l1 TEXT, cause_l2 TEXT, cause_l3 TEXT, cause_l4 TEXT);
CREATE INDEX IF NOT EXISTS idx_occ_cause ON issue_occurrence(cause_l1,cause_l2,cause_l3,cause_l4);
CREATE TABLE IF NOT EXISTS issue_escape (knowledge_id TEXT PRIMARY KEY REFERENCES issue_record(knowledge_id), is_escape TEXT, escape_type TEXT, original_reason TEXT, root_cause_original TEXT, escape_l1 TEXT, escape_l2 TEXT, escape_l3 TEXT, escape_l4 TEXT);
CREATE INDEX IF NOT EXISTS idx_escape_cause ON issue_escape(escape_l1,escape_l2,escape_l3,escape_l4);
CREATE TABLE IF NOT EXISTS issue_solution (knowledge_id TEXT PRIMARY KEY REFERENCES issue_record(knowledge_id), original_solution TEXT, corrective_action TEXT, improvement_action TEXT, management_action TEXT, technical_action TEXT, reusable_action TEXT);
CREATE TABLE IF NOT EXISTS issue_verification (knowledge_id TEXT PRIMARY KEY REFERENCES issue_record(knowledge_id), existing_case TEXT, mandatory_test TEXT, automated TEXT, mandatory_not_automated TEXT, extracted_test_scenario TEXT);
CREATE TABLE IF NOT EXISTS issue_product_extension (knowledge_id TEXT PRIMARY KEY REFERENCES issue_record(knowledge_id), extension_json TEXT NOT NULL);
'''

class SqliteIssueKnowledgeRepository:
    def __init__(self, db_path: str|Path):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True, exist_ok=True); self.initialize()
    def connect(self):
        c=sqlite3.connect(self.db_path); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
    def initialize(self):
        with self.connect() as c: c.executescript(SCHEMA)
    def begin_batch(self,batch_id,business_type,source_file):
        with self.connect() as c: c.execute('INSERT OR REPLACE INTO import_batch(id,business_type,source_file,status) VALUES(?,?,?,?)',(batch_id,business_type,source_file,'RUNNING'))
    def finish_batch(self,batch_id,stats,status='COMPLETED'):
        with self.connect() as c: c.execute('UPDATE import_batch SET completed_at=CURRENT_TIMESTAMP,status=?,total_rows=?,imported_rows=?,skipped_rows=?,failed_rows=? WHERE id=?',(status,stats['total'],stats['imported'],stats['skipped'],stats['failed'],batch_id))
    def exists_source(self,business_type,issue_id,source_hash):
        with self.connect() as c:
            return c.execute('''SELECT 1 FROM issue_record r JOIN issue_source_raw s ON s.knowledge_id=r.knowledge_id WHERE r.business_type=? AND r.issue_id=? AND s.source_hash=? LIMIT 1''',(business_type,issue_id,source_hash)).fetchone() is not None
    def save(self, q: QualityIssueDTO):
        f=q.issue_fact; p=q.product_context; o=q.occurrence; e=q.escape; s=q.solution; v=q.verification
        with self.connect() as c:
            c.execute('''INSERT INTO issue_record(knowledge_id,business_type,issue_id,title,description,impact,severity,issue_type,is_defect,industry,customer,month,product,platform,department,business_group) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(q.identity.knowledge_id,q.identity.business_type,q.identity.issue_id,f.title,f.description,f.impact,f.severity,f.issue_type,f.is_defect,f.industry,f.customer,f.month,f.product,f.platform,f.department,f.business_group))
            c.execute('INSERT INTO issue_source_raw(knowledge_id,source_file,source_sheet,source_row,raw_json,import_batch_id,source_hash) VALUES(?,?,?,?,?,?,?)',(q.identity.knowledge_id,q.source.source_file,q.source.source_sheet,q.source.source_row,json.dumps(q.raw_record,ensure_ascii=False,default=str),q.source.source_import_batch,q.source.source_hash))
            c.execute('INSERT INTO issue_product_context VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(q.identity.knowledge_id,p.product,p.product_series,p.platform,p.module,p.business_group,p.feature_l1,p.feature_l2,p.feature_l3,p.feature_l4,p.fa_feature,p.fa_l1_feature,p.layer,p.layer_category))
            c.execute('INSERT INTO issue_occurrence VALUES(?,?,?,?,?,?,?)',(q.identity.knowledge_id,o.original_reason,o.root_cause_original,o.cause_l1,o.cause_l2,o.cause_l3,o.cause_l4))
            c.execute('INSERT INTO issue_escape VALUES(?,?,?,?,?,?,?,?,?)',(q.identity.knowledge_id,e.is_escape,e.escape_type,e.original_reason,e.root_cause_original,e.escape_l1,e.escape_l2,e.escape_l3,e.escape_l4))
            c.execute('INSERT INTO issue_solution VALUES(?,?,?,?,?,?,?)',(q.identity.knowledge_id,s.original_solution,s.corrective_action,s.improvement_action,s.management_action,s.technical_action,s.reusable_action))
            c.execute('INSERT INTO issue_verification VALUES(?,?,?,?,?,?)',(q.identity.knowledge_id,v.existing_case,v.mandatory_test,v.automated,v.mandatory_not_automated,v.extracted_test_scenario))
            c.execute('INSERT INTO issue_product_extension VALUES(?,?)',(q.identity.knowledge_id,json.dumps(q.product_extension,ensure_ascii=False)))
    def query(self, filters: dict[str,Any]|None=None, limit=100):
        filters=filters or {}; allowed={'business_type':'r.business_type','issue_id':'r.issue_id','product':'r.product','platform':'r.platform','is_escape':'e.is_escape','occurrence_l1':'o.cause_l1','escape_l1':'e.escape_l1'}
        where=[]; vals=[]
        for k,v in filters.items():
            if k not in allowed: raise ValueError(f'Unsupported filter: {k}')
            where.append(f'{allowed[k]} = ?'); vals.append(v)
        sql='''SELECT r.*, o.cause_l1 occurrence_l1,o.cause_l2 occurrence_l2,e.is_escape,e.escape_l1,e.escape_l2 FROM issue_record r LEFT JOIN issue_occurrence o USING(knowledge_id) LEFT JOIN issue_escape e USING(knowledge_id)'''
        if where: sql+=' WHERE '+' AND '.join(where)
        sql+=' ORDER BY r.id DESC LIMIT ?'; vals.append(limit)
        with self.connect() as c: return [dict(x) for x in c.execute(sql,vals).fetchall()]
    def get(self, knowledge_id):
        rows=self.query({},100000)
        return next((r for r in rows if r['knowledge_id']==knowledge_id),None)

# M2 schema extension is applied lazily to preserve compatibility with existing M1 databases.
M2_SCHEMA = '''
CREATE TABLE IF NOT EXISTS analysis_run (
 analysis_run_id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL REFERENCES issue_record(knowledge_id),
 analysis_type TEXT NOT NULL, model_provider TEXT, model_name TEXT, prompt_name TEXT, prompt_version TEXT,
 schema_version TEXT, engine_version TEXT, started_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT,
 status TEXT NOT NULL, input_hash TEXT, error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_analysis_run_knowledge ON analysis_run(knowledge_id, analysis_type, started_at);
CREATE TABLE IF NOT EXISTS issue_ai_analysis (
 id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_id TEXT NOT NULL REFERENCES issue_record(knowledge_id),
 analysis_run_id TEXT NOT NULL REFERENCES analysis_run(analysis_run_id), analysis_type TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS issue_capability_gap (
 id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_id TEXT NOT NULL REFERENCES issue_record(knowledge_id),
 analysis_run_id TEXT NOT NULL REFERENCES analysis_run(analysis_run_id), gap_dimension TEXT NOT NULL,
 gap_category TEXT, gap_description TEXT, recommended_control TEXT, scope TEXT, confidence REAL,
 evidence_json TEXT, model_version TEXT, prompt_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gap_dimension ON issue_capability_gap(gap_dimension,gap_category);
'''

def _ensure_m2(self):
    with self.connect() as c: c.executescript(M2_SCHEMA)
SqliteIssueKnowledgeRepository.ensure_m2 = _ensure_m2

def _get_analysis_context(self, knowledge_id):
    self.ensure_m2()
    with self.connect() as c:
        r=c.execute('''SELECT r.*, p.product_series,p.module,p.feature_l1,p.feature_l2,p.feature_l3,p.feature_l4,p.fa_feature,p.fa_l1_feature,
        o.original_reason occurrence_original_reason,o.root_cause_original occurrence_root_cause,o.cause_l1 occurrence_l1,o.cause_l2 occurrence_l2,o.cause_l3 occurrence_l3,o.cause_l4 occurrence_l4,
        e.is_escape,e.escape_type,e.original_reason escape_original_reason,e.root_cause_original escape_root_cause,e.escape_l1,e.escape_l2,e.escape_l3,e.escape_l4,
        s.original_solution,s.corrective_action,s.improvement_action,s.management_action,s.technical_action,s.reusable_action,
        v.existing_case,v.mandatory_test,v.automated,v.mandatory_not_automated,v.extracted_test_scenario,x.extension_json,raw.raw_json
        FROM issue_record r LEFT JOIN issue_product_context p USING(knowledge_id) LEFT JOIN issue_occurrence o USING(knowledge_id)
        LEFT JOIN issue_escape e USING(knowledge_id) LEFT JOIN issue_solution s USING(knowledge_id)
        LEFT JOIN issue_verification v USING(knowledge_id) LEFT JOIN issue_product_extension x USING(knowledge_id)
        LEFT JOIN issue_source_raw raw USING(knowledge_id) WHERE r.knowledge_id=? ORDER BY raw.id DESC LIMIT 1''',(knowledge_id,)).fetchone()
        if not r: return None
        d=dict(r)
        for k in ('extension_json','raw_json'):
            try: d[k]=json.loads(d.get(k) or '{}')
            except Exception: d[k]={}
        return d
SqliteIssueKnowledgeRepository.get_analysis_context = _get_analysis_context

def _start_run(self, run):
    self.ensure_m2()
    with self.connect() as c:
        c.execute('''INSERT INTO analysis_run(analysis_run_id,knowledge_id,analysis_type,model_provider,model_name,prompt_name,prompt_version,schema_version,engine_version,status,input_hash)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(run['analysis_run_id'],run['knowledge_id'],run['analysis_type'],run.get('model_provider'),run.get('model_name'),run.get('prompt_name'),run.get('prompt_version'),run.get('schema_version'),run.get('engine_version'),run.get('status','RUNNING'),run.get('input_hash')))
SqliteIssueKnowledgeRepository.start_analysis_run = _start_run

def _finish_run(self, run_id,status,model_name=None,error_message=None):
    with self.connect() as c: c.execute('UPDATE analysis_run SET status=?,model_name=COALESCE(?,model_name),error_message=?,completed_at=CURRENT_TIMESTAMP WHERE analysis_run_id=?',(status,model_name,error_message,run_id))
SqliteIssueKnowledgeRepository.finish_analysis_run = _finish_run

def _save_analysis(self, knowledge_id,run_id,analysis_type,result):
    with self.connect() as c: c.execute('INSERT INTO issue_ai_analysis(knowledge_id,analysis_run_id,analysis_type,result_json) VALUES(?,?,?,?)',(knowledge_id,run_id,analysis_type,json.dumps(result,ensure_ascii=False,default=str)))
SqliteIssueKnowledgeRepository.save_analysis = _save_analysis

def _save_gaps(self, knowledge_id,run_id,gaps,model_version='',prompt_version=''):
    with self.connect() as c:
        for g in gaps:
            d=g.model_dump() if hasattr(g,'model_dump') else g
            c.execute('''INSERT INTO issue_capability_gap(knowledge_id,analysis_run_id,gap_dimension,gap_category,gap_description,recommended_control,scope,confidence,evidence_json,model_version,prompt_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(knowledge_id,run_id,d.get('gap_dimension') or d.get('dimension'),d.get('gap_category') or d.get('category'),d.get('gap_description') or d.get('description'),d.get('recommended_control'),d.get('scope'),d.get('confidence'),json.dumps(d.get('evidence_refs') or [],ensure_ascii=False,default=str),model_version,prompt_version))
SqliteIssueKnowledgeRepository.save_capability_gaps = _save_gaps

def _latest_analysis(self,knowledge_id,analysis_type):
    self.ensure_m2()
    with self.connect() as c:
        r=c.execute('''SELECT a.result_json,ar.* FROM issue_ai_analysis a JOIN analysis_run ar USING(analysis_run_id) WHERE a.knowledge_id=? AND a.analysis_type=? AND ar.status='SUCCESS' ORDER BY a.id DESC LIMIT 1''',(knowledge_id,analysis_type)).fetchone()
        if not r:return None
        d=dict(r); d['result']=json.loads(d.pop('result_json')); return d
SqliteIssueKnowledgeRepository.latest_analysis = _latest_analysis

# M3 Query / Aggregation extensions.
M3_INDEXES = '''
CREATE INDEX IF NOT EXISTS idx_issue_month ON issue_record(month);
CREATE INDEX IF NOT EXISTS idx_issue_severity ON issue_record(severity);
CREATE INDEX IF NOT EXISTS idx_gap_knowledge ON issue_capability_gap(knowledge_id, created_at);
CREATE INDEX IF NOT EXISTS idx_gap_category ON issue_capability_gap(gap_category);
'''

def _ensure_m3(self):
    self.ensure_m2()
    with self.connect() as c:
        c.executescript(M3_INDEXES)
SqliteIssueKnowledgeRepository.ensure_m3 = _ensure_m3


def _query_m3(self, filters: dict[str,Any]|None=None, limit=100):
    self.ensure_m3()
    filters=filters or {}
    allowed={
        'knowledge_id':'r.knowledge_id','business_type':'r.business_type','issue_id':'r.issue_id','product':'r.product','platform':'r.platform',
        'severity':'r.severity','issue_type':'r.issue_type','month':'r.month','industry':'r.industry','is_defect':'r.is_defect',
        'is_escape':'e.is_escape','occurrence_l1':'o.cause_l1','occurrence_l2':'o.cause_l2','occurrence_l3':'o.cause_l3','occurrence_l4':'o.cause_l4',
        'escape_l1':'e.escape_l1','escape_l2':'e.escape_l2','escape_l3':'e.escape_l3','escape_l4':'e.escape_l4',
        'module':'p.module','feature_l1':'p.feature_l1','feature_l2':'p.feature_l2','recurrence_risk_level':'rr.risk_level',
    }
    where=[]; vals=[]
    for k,v in filters.items():
        if k in {'gap_dimension','gap_category'}:
            where.append("EXISTS (SELECT 1 FROM issue_capability_gap g WHERE g.knowledge_id=r.knowledge_id AND g.%s=?)" % ('gap_dimension' if k=='gap_dimension' else 'gap_category'))
            vals.append(v); continue
        if k not in allowed: raise ValueError(f'Unsupported filter: {k}')
        where.append(f'{allowed[k]} = ?'); vals.append(v)
    sql='''
    WITH latest_recurrence AS (
      SELECT a.knowledge_id,
             json_extract(a.result_json,'$.recurrence_risk_level') AS risk_level,
             ROW_NUMBER() OVER (PARTITION BY a.knowledge_id ORDER BY a.id DESC) rn
      FROM issue_ai_analysis a JOIN analysis_run ar USING(analysis_run_id)
      WHERE a.analysis_type='recurrence' AND ar.status='SUCCESS'
    )
    SELECT r.*, p.product_series,p.module,p.feature_l1,p.feature_l2,p.feature_l3,p.feature_l4,p.fa_feature,p.fa_l1_feature,
           o.original_reason occurrence_original_reason,o.root_cause_original occurrence_root_cause,
           o.cause_l1 occurrence_l1,o.cause_l2 occurrence_l2,o.cause_l3 occurrence_l3,o.cause_l4 occurrence_l4,
           e.is_escape,e.escape_type,e.original_reason escape_original_reason,e.root_cause_original escape_root_cause,
           e.escape_l1,e.escape_l2,e.escape_l3,e.escape_l4,
           s.original_solution,s.corrective_action,s.improvement_action,s.management_action,s.technical_action,s.reusable_action,
           rr.risk_level recurrence_risk_level
    FROM issue_record r
    LEFT JOIN issue_product_context p USING(knowledge_id)
    LEFT JOIN issue_occurrence o USING(knowledge_id)
    LEFT JOIN issue_escape e USING(knowledge_id)
    LEFT JOIN issue_solution s USING(knowledge_id)
    LEFT JOIN latest_recurrence rr ON rr.knowledge_id=r.knowledge_id AND rr.rn=1
    '''
    if where: sql+=' WHERE '+' AND '.join(where)
    sql+=' ORDER BY r.id DESC LIMIT ?'; vals.append(int(limit))
    with self.connect() as c: return [dict(x) for x in c.execute(sql,vals).fetchall()]
SqliteIssueKnowledgeRepository.query = _query_m3


def _query_capability_gaps(self, filters=None, limit=100):
    self.ensure_m3(); filters=filters or {}
    allowed={'knowledge_id':'g.knowledge_id','business_type':'r.business_type','issue_id':'r.issue_id','product':'r.product','platform':'r.platform','gap_dimension':'g.gap_dimension','gap_category':'g.gap_category','scope':'g.scope'}
    where=[]; vals=[]
    for k,v in filters.items():
        if k not in allowed: raise ValueError(f'Unsupported capability gap filter: {k}')
        where.append(f'{allowed[k]}=?'); vals.append(v)
    sql='''SELECT g.*,r.business_type,r.issue_id,r.product,r.platform FROM issue_capability_gap g JOIN issue_record r USING(knowledge_id)'''
    if where: sql+=' WHERE '+' AND '.join(where)
    sql+=' ORDER BY g.id DESC LIMIT ?'; vals.append(int(limit))
    with self.connect() as c: return [dict(x) for x in c.execute(sql,vals).fetchall()]
SqliteIssueKnowledgeRepository.query_capability_gaps = _query_capability_gaps


def _aggregate(self, kind, *, level='l1', business_type=None, limit=20):
    self.ensure_m3()
    if kind not in {'occurrence','escape'}: raise ValueError('kind must be occurrence or escape')
    if level not in {'l1','l2','l3','l4'}: raise ValueError('level must be l1..l4')
    col=('cause_' if kind=='occurrence' else 'escape_')+level
    table='issue_occurrence' if kind=='occurrence' else 'issue_escape'
    where=[f"COALESCE(TRIM(x.{col}),'')<>''"]; vals=[]
    if business_type: where.append('r.business_type=?'); vals.append(business_type)
    sql=f'''SELECT x.{col} value, COUNT(*) count FROM {table} x JOIN issue_record r USING(knowledge_id) WHERE {' AND '.join(where)} GROUP BY x.{col} ORDER BY count DESC, value LIMIT ?'''
    vals.append(int(limit))
    with self.connect() as c: return [dict(x) for x in c.execute(sql,vals).fetchall()]
SqliteIssueKnowledgeRepository.aggregate = _aggregate


def _aggregate_capability_gaps(self, *, business_type=None, dimension=None, limit=20):
    self.ensure_m3(); where=["COALESCE(TRIM(g.gap_category),'')<>''"]; vals=[]
    if business_type: where.append('r.business_type=?'); vals.append(business_type)
    if dimension: where.append('g.gap_dimension=?'); vals.append(dimension)
    sql=f'''SELECT g.gap_dimension,g.gap_category,COUNT(*) count,COUNT(DISTINCT g.knowledge_id) issue_count,COUNT(DISTINCT r.business_type) business_count
            FROM issue_capability_gap g JOIN issue_record r USING(knowledge_id)
            WHERE {' AND '.join(where)} GROUP BY g.gap_dimension,g.gap_category ORDER BY count DESC,g.gap_category LIMIT ?'''
    vals.append(int(limit))
    with self.connect() as c: return [dict(x) for x in c.execute(sql,vals).fetchall()]
SqliteIssueKnowledgeRepository.aggregate_capability_gaps = _aggregate_capability_gaps


def _query_analysis_runs(self, filters=None, limit=100):
    self.ensure_m3(); filters=filters or {}
    allowed={'analysis_run_id':'ar.analysis_run_id','knowledge_id':'ar.knowledge_id','business_type':'r.business_type','issue_id':'r.issue_id','analysis_type':'ar.analysis_type','status':'ar.status'}
    where=[]; vals=[]
    for k,v in filters.items():
        if k not in allowed: raise ValueError(f'Unsupported analysis run filter: {k}')
        where.append(f'{allowed[k]}=?'); vals.append(v)
    sql='''SELECT ar.*,r.business_type,r.issue_id FROM analysis_run ar JOIN issue_record r USING(knowledge_id)'''
    if where: sql+=' WHERE '+' AND '.join(where)
    sql+=' ORDER BY ar.started_at DESC,ar.analysis_run_id DESC LIMIT ?'; vals.append(int(limit))
    with self.connect() as c: return [dict(x) for x in c.execute(sql,vals).fetchall()]
SqliteIssueKnowledgeRepository.query_analysis_runs = _query_analysis_runs


def _statistics(self, *, business_type=None, limit=20):
    self.ensure_m3(); bwhere=' WHERE r.business_type=?' if business_type else ''; bvals=[business_type] if business_type else []
    with self.connect() as c:
        by_business=[dict(x) for x in c.execute('SELECT business_type,COUNT(*) count FROM issue_record GROUP BY business_type ORDER BY count DESC').fetchall()]
        risk_sql='''WITH latest AS (SELECT a.knowledge_id,json_extract(a.result_json,'$.recurrence_risk_level') risk_level,ROW_NUMBER() OVER(PARTITION BY a.knowledge_id ORDER BY a.id DESC) rn FROM issue_ai_analysis a JOIN analysis_run ar USING(analysis_run_id) WHERE a.analysis_type='recurrence' AND ar.status='SUCCESS') SELECT l.risk_level,COUNT(*) count FROM latest l JOIN issue_record r USING(knowledge_id) WHERE l.rn=1'''
        vals=[]
        if business_type: risk_sql+=' AND r.business_type=?'; vals.append(business_type)
        risk_sql+=' GROUP BY l.risk_level ORDER BY count DESC'
        risk=[dict(x) for x in c.execute(risk_sql,vals).fetchall()]
        cross_sql='''SELECT g.gap_dimension,g.gap_category,COUNT(DISTINCT r.business_type) business_count,COUNT(DISTINCT g.knowledge_id) count,
                    GROUP_CONCAT(DISTINCT r.business_type) businesses
                    FROM issue_capability_gap g JOIN issue_record r USING(knowledge_id)
                    GROUP BY g.gap_dimension,g.gap_category HAVING COUNT(DISTINCT r.business_type)>=2
                    ORDER BY business_count DESC,count DESC LIMIT ?'''
        cross=[dict(x) for x in c.execute(cross_sql,(int(limit),)).fetchall()]
    return {
        'by_business':by_business,
        'top_occurrence':self.aggregate('occurrence',business_type=business_type,limit=limit),
        'top_escape':self.aggregate('escape',business_type=business_type,limit=limit),
        'top_capability_gap':self.aggregate_capability_gaps(business_type=business_type,limit=limit),
        'recurrence_risk':risk,
        'cross_business_capability_gaps':cross,
    }
SqliteIssueKnowledgeRepository.statistics = _statistics
