from __future__ import annotations
import json, sqlite3, uuid
from pathlib import Path
from .models import MappingConfiguration, MappingItem, MAPPING_STATUSES, TARGET_DOMAINS, VALIDATION_LEVELS

MAPPING_SCHEMA_VERSION=1
MAPPING_SCHEMA='''
CREATE TABLE IF NOT EXISTS knowledge_schema_version(
 component TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS mapping_config(
 config_id TEXT PRIMARY KEY,business_type TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,
 source_type TEXT NOT NULL,source_file TEXT,source_hash TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP,created_by TEXT,activated_at TEXT,metadata_json TEXT NOT NULL DEFAULT '{}',
 UNIQUE(business_type,version));
CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_active_business ON mapping_config(business_type) WHERE status='ACTIVE';
CREATE INDEX IF NOT EXISTS idx_mapping_config_business ON mapping_config(business_type,version DESC);
CREATE TABLE IF NOT EXISTS mapping_item(
 mapping_id TEXT PRIMARY KEY,config_id TEXT NOT NULL REFERENCES mapping_config(config_id) ON DELETE CASCADE,
 canonical_field TEXT NOT NULL,target_domain TEXT NOT NULL,target_field TEXT NOT NULL,required INTEGER NOT NULL DEFAULT 0,
 enabled INTEGER NOT NULL DEFAULT 1,description TEXT,display_order INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_mapping_item_config ON mapping_item(config_id,display_order,mapping_id);
CREATE TABLE IF NOT EXISTS mapping_alias(
 id INTEGER PRIMARY KEY AUTOINCREMENT,mapping_id TEXT NOT NULL REFERENCES mapping_item(mapping_id) ON DELETE CASCADE,
 alias TEXT NOT NULL,alias_type TEXT NOT NULL DEFAULT 'ALIAS',display_order INTEGER NOT NULL DEFAULT 0,
 UNIQUE(mapping_id,alias,alias_type));
CREATE TABLE IF NOT EXISTS mapping_validation_result(
 id INTEGER PRIMARY KEY AUTOINCREMENT,config_id TEXT NOT NULL REFERENCES mapping_config(config_id) ON DELETE CASCADE,
 validation_run_id TEXT NOT NULL,level TEXT NOT NULL,code TEXT NOT NULL,message TEXT NOT NULL,mapping_id TEXT,
 details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mapping_validation_config ON mapping_validation_result(config_id,validation_run_id);
CREATE TABLE IF NOT EXISTS mapping_migration_run(
 migration_run_id TEXT PRIMARY KEY,business_type TEXT,source_type TEXT NOT NULL,source_file TEXT,source_hash TEXT,
 migration_tool_version TEXT,status TEXT NOT NULL,dry_run INTEGER NOT NULL DEFAULT 1,result_json TEXT NOT NULL DEFAULT '{}',
 started_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_mapping_migration_hash ON mapping_migration_run(business_type,source_hash,status);
CREATE TABLE IF NOT EXISTS mapping_export_run(
 export_run_id TEXT PRIMARY KEY,config_id TEXT NOT NULL REFERENCES mapping_config(config_id),format TEXT NOT NULL,
 target_file TEXT,status TEXT NOT NULL,result_json TEXT NOT NULL DEFAULT '{}',created_at TEXT DEFAULT CURRENT_TIMESTAMP,completed_at TEXT);
'''

def _j(v): return json.dumps(v or {},ensure_ascii=False,default=str)

class MappingConfigurationRepository:
    def __init__(self, db_path:str|Path):
        self.db_path=Path(db_path); self.db_path.parent.mkdir(parents=True,exist_ok=True); self.initialize_schema()
    def connect(self):
        c=sqlite3.connect(self.db_path); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
    def initialize_schema(self):
        with self.connect() as c:
            c.executescript(MAPPING_SCHEMA)
            c.execute("INSERT INTO knowledge_schema_version(component,version) VALUES('mapping',?) ON CONFLICT(component) DO UPDATE SET version=MAX(version,excluded.version),updated_at=CURRENT_TIMESTAMP",(MAPPING_SCHEMA_VERSION,))
    def schema_version(self):
        with self.connect() as c:return c.execute("SELECT version FROM knowledge_schema_version WHERE component='mapping'").fetchone()[0]
    def next_version(self,business_type):
        with self.connect() as c:return c.execute('SELECT COALESCE(MAX(version),0)+1 FROM mapping_config WHERE business_type=?',(business_type,)).fetchone()[0]
    def create_config(self,config:MappingConfiguration):
        if config.status not in MAPPING_STATUSES: raise ValueError('invalid mapping status')
        with self.connect() as c:
            c.execute('''INSERT INTO mapping_config(config_id,business_type,version,status,source_type,source_file,source_hash,created_by,activated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',(config.config_id,config.business_type,config.version,config.status,config.source_type,config.source_file,config.source_hash,config.created_by,config.activated_at,_j(config.metadata)))
            for item in config.mappings:self._insert_item(c,config.config_id,item)
        return self.get_config(config.config_id)
    def create_draft(self,business_type,*,source_type='WEB',source_file=None,source_hash=None,created_by=None,metadata=None,mappings=None):
        cfg=MappingConfiguration('MAP-'+uuid.uuid4().hex,business_type,self.next_version(business_type),'DRAFT',source_type,source_file,source_hash,created_by,None,metadata or {},mappings or [])
        return self.create_config(cfg)
    def _insert_item(self,c,config_id,item:MappingItem):
        if item.target_domain not in TARGET_DOMAINS: raise ValueError('invalid target domain')
        c.execute('INSERT INTO mapping_item(mapping_id,config_id,canonical_field,target_domain,target_field,required,enabled,description,display_order) VALUES(?,?,?,?,?,?,?,?,?)',(item.mapping_id,config_id,item.canonical_field,item.target_domain,item.target_field,int(item.required),int(item.enabled),item.description,item.display_order))
        for alias_type,values in (('SOURCE_HEADER',item.source_headers),('ALIAS',item.aliases)):
            seen=set(); order=0
            for value in values:
                alias=str(value).strip()
                if not alias or alias in seen: continue
                seen.add(alias)
                c.execute('INSERT INTO mapping_alias(mapping_id,alias,alias_type,display_order) VALUES(?,?,?,?)',(item.mapping_id,alias,alias_type,order))
                order+=1
    def get_config(self,config_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM mapping_config WHERE config_id=?',(config_id,)).fetchone()
            return self._hydrate(c,row) if row else None
    def get_effective_config(self,business_type):
        with self.connect() as c:
            row=c.execute("SELECT * FROM mapping_config WHERE business_type=? AND status='ACTIVE'",(business_type,)).fetchone()
            return self._hydrate(c,row) if row else None
    def list_configs(self,business_type=None):
        with self.connect() as c:
            rows=c.execute('SELECT * FROM mapping_config'+(' WHERE business_type=?' if business_type else '')+' ORDER BY business_type,version DESC',((business_type,) if business_type else ())).fetchall()
            return [self._hydrate(c,r) for r in rows]
    def _hydrate(self,c,row):
        d=dict(row); d['metadata']=json.loads(d.pop('metadata_json') or '{}'); items=[]
        for r in c.execute('SELECT * FROM mapping_item WHERE config_id=? ORDER BY display_order,mapping_id',(d['config_id'],)).fetchall():
            x=dict(r); aliases=c.execute('SELECT alias,alias_type FROM mapping_alias WHERE mapping_id=? ORDER BY display_order,id',(x['mapping_id'],)).fetchall(); x['source_headers']=[a['alias'] for a in aliases if a['alias_type']=='SOURCE_HEADER']; x['aliases']=[a['alias'] for a in aliases if a['alias_type']=='ALIAS']; x['required']=bool(x['required']);x['enabled']=bool(x['enabled']);x.pop('config_id',None);items.append(x)
        d['mappings']=items; return d
    def save_validation_results(self,config_id,results,validation_run_id=None):
        rid=validation_run_id or 'VAL-'+uuid.uuid4().hex
        with self.connect() as c:
            for r in results:
                level=r['level'].upper()
                if level not in VALIDATION_LEVELS: raise ValueError('invalid validation level')
                c.execute('INSERT INTO mapping_validation_result(config_id,validation_run_id,level,code,message,mapping_id,details_json) VALUES(?,?,?,?,?,?,?)',(config_id,rid,level,r['code'],r['message'],r.get('mapping_id'),_j(r.get('details'))))
        return rid
    def get_validation_results(self,config_id):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM mapping_validation_result WHERE config_id=? ORDER BY id',(config_id,)).fetchall()]
    def activate(self,config_id):
        with self.connect() as c:
            row=c.execute('SELECT business_type,status FROM mapping_config WHERE config_id=?',(config_id,)).fetchone()
            if not row: raise KeyError(config_id)
            errors=c.execute("SELECT COUNT(*) FROM mapping_validation_result WHERE config_id=? AND level='ERROR'",(config_id,)).fetchone()[0]
            if errors: raise ValueError('mapping config has validation ERROR')
            c.execute("UPDATE mapping_config SET status='INACTIVE',updated_at=CURRENT_TIMESTAMP WHERE business_type=? AND status='ACTIVE'",(row['business_type'],))
            c.execute("UPDATE mapping_config SET status='ACTIVE',activated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE config_id=?",(config_id,))
        return self.get_config(config_id)
    def deactivate(self,config_id):
        with self.connect() as c:c.execute("UPDATE mapping_config SET status='INACTIVE',updated_at=CURRENT_TIMESTAMP WHERE config_id=?",(config_id,))
    def start_migration_run(self,*,business_type=None,source_type='YAML_MIGRATION',source_file=None,source_hash=None,tool_version='A1',dry_run=True):
        rid='MIG-'+uuid.uuid4().hex
        with self.connect() as c:c.execute('INSERT INTO mapping_migration_run(migration_run_id,business_type,source_type,source_file,source_hash,migration_tool_version,status,dry_run) VALUES(?,?,?,?,?,?,?,?)',(rid,business_type,source_type,source_file,source_hash,tool_version,'RUNNING',int(dry_run)))
        return rid
    def find_successful_migration(self,business_type,source_hash):
        with self.connect() as c:
            r=c.execute("SELECT * FROM mapping_migration_run WHERE business_type=? AND source_hash=? AND status='COMPLETED' AND dry_run=0 ORDER BY completed_at DESC LIMIT 1",(business_type,source_hash)).fetchone()
            return dict(r) if r else None
    def list_migration_runs(self,business_type=None):
        with self.connect() as c:
            q='SELECT * FROM mapping_migration_run'+(' WHERE business_type=?' if business_type else '')+' ORDER BY started_at DESC'
            return [dict(r) for r in c.execute(q,((business_type,) if business_type else ())).fetchall()]
    def finish_migration_run(self,run_id,status,result=None):
        with self.connect() as c:c.execute('UPDATE mapping_migration_run SET status=?,result_json=?,completed_at=CURRENT_TIMESTAMP WHERE migration_run_id=?',(status,_j(result),run_id))

    def replace_draft_mappings(self, config_id, mappings):
        row=self.get_config(config_id)
        if not row: raise KeyError(config_id)
        if row['status']!='DRAFT': raise ValueError('only DRAFT mapping can be edited')
        with self.connect() as c:
            c.execute('DELETE FROM mapping_item WHERE config_id=?',(config_id,))
            for item in mappings: self._insert_item(c,config_id,item)
            c.execute("UPDATE mapping_config SET updated_at=CURRENT_TIMESTAMP WHERE config_id=?",(config_id,))
        return self.get_config(config_id)

    def clear_validation_results(self, config_id):
        with self.connect() as c: c.execute('DELETE FROM mapping_validation_result WHERE config_id=?',(config_id,))

    def latest_migration_for_config(self, config_id):
        cfg=self.get_config(config_id)
        if not cfg or not cfg.get('source_hash'): return None
        with self.connect() as c:
            r=c.execute('SELECT * FROM mapping_migration_run WHERE business_type=? AND source_hash=? ORDER BY started_at DESC LIMIT 1',(cfg['business_type'],cfg['source_hash'])).fetchone()
            return dict(r) if r else None
