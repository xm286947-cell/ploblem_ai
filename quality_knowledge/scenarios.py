from __future__ import annotations

import json
import sqlite3
import uuid


LIFECYCLES = (
    ("ENGINEERING_CONFIGURATION", "工程配置", "把控制需求转化为可执行、可调试的PLC工程"),
    ("SOFTWARE_DEBUGGING", "软件调试", "把能编译的工程调成能正确工作的工程"),
    ("RUNTIME_EXECUTION", "运行执行", "保证PLC控制逻辑实时、正确、稳定地执行"),
    ("SYSTEM_INTEGRATION", "系统联动", "保证PLC与其他设备、控制系统共同完成完整业务过程"),
    ("LONG_TERM_OPERATION", "长稳运行", "保证系统在长时间、高频、复杂条件下持续稳定"),
    ("VERSION_MAINTENANCE", "版本维护", "保证已交付系统在持续变化中安全、兼容、可恢复"),
)

ACTIVITIES = (
    ("ENGINEERING_CONFIGURATION","PROJECT_INIT","工程创建与初始化","新建工程 → 选择PLC → 初始化工程环境 → 进入工程配置"),
    ("ENGINEERING_CONFIGURATION","HARDWARE_CONFIGURATION","硬件与设备组态","工程创建 → PLC配置 → IO配置 → 设备资源建立"),
    ("ENGINEERING_CONFIGURATION","NETWORK_CONFIGURATION","网络与通信配置","设备组态 → 网络拓扑 → 通信参数 → 通信关系"),
    ("ENGINEERING_CONFIGURATION","CONTROL_PARAMETER_CONFIGURATION","设备与控制参数配置","设备组态 → 参数设置 → 参数检查 → 配置完成"),
    ("ENGINEERING_CONFIGURATION","VARIABLE_DATA_MODELING","变量与数据建模","设备配置 → IO映射 → 变量定义 → 数据结构 → 程序使用"),
    ("ENGINEERING_CONFIGURATION","CONTROL_PROGRAMMING","控制程序开发","控制需求 → 程序架构 → 逻辑开发 → 功能块调用"),
    ("ENGINEERING_CONFIGURATION","BUILD_VALIDATION","工程编译与校验","配置编程 → 编译 → 错误检查 → 修改 → 编译通过"),
    ("SOFTWARE_DEBUGGING","PLC_DISCOVERY_CONNECTION","PLC发现与连接","工程准备 → 查找PLC → 网络连接 → 在线建立"),
    ("SOFTWARE_DEBUGGING","DOWNLOAD_START","工程下载与启动","PLC连接 → 下载配置/程序 → 初始化 → RUN"),
    ("SOFTWARE_DEBUGGING","ONLINE_MONITORING","程序在线监控与调试","下载运行 → 在线监控 → 变量观察 → 状态分析 → 调整"),
    ("SOFTWARE_DEBUGGING","VARIABLE_IO_DEBUG","变量与IO调试","在线运行 → 变量监控/写值 → IO检查 → 功能验证"),
    ("SOFTWARE_DEBUGGING","CONTROL_FUNCTION_DEBUG","控制功能调试","单点验证 → 功能动作 → 顺序验证 → 参数调整"),
    ("SOFTWARE_DEBUGGING","PROBLEM_DIAGNOSIS","运行问题分析与诊断","发现异常 → 信息采集 → Trace/诊断 → 定位 → 验证"),
    ("RUNTIME_EXECUTION","CONTROL_PROGRAM_EXECUTION","PLC控制程序执行","输入采集 → 程序执行 → 逻辑计算 → 输出更新"),
    ("RUNTIME_EXECUTION","TASK_CYCLE_EXECUTION","任务与周期执行","任务触发 → 程序调度 → 执行 → 周期完成"),
    ("RUNTIME_EXECUTION","STATE_DATA_PROCESSING","设备状态与数据处理","数据采集 → 状态判断 → 运算 → 状态更新"),
    ("RUNTIME_EXECUTION","POWER_LOSS_RETENTION_RECOVERY","掉电数据保持与上电恢复","正常运行 → 关键数据/状态产生 → 掉电 → 数据保持 → 重新上电 → 数据恢复 → 程序继续运行"),
    ("RUNTIME_EXECUTION","RUNTIME_EXCEPTION_HANDLING","运行异常处理","运行 → 异常触发 → 识别 → 安全逻辑 → 恢复/停机"),
    ("SYSTEM_INTEGRATION","FIELD_DEVICE_INTEGRATION","PLC与现场设备联动","PLC逻辑 → IO/驱动指令 → 设备动作 → 状态反馈"),
    ("SYSTEM_INTEGRATION","MULTI_DEVICE_SEQUENCE","多设备顺序联动","设备A完成 → 状态握手 → PLC判断 → 设备B启动"),
    ("SYSTEM_INTEGRATION","PLC_SYSTEM_INTEGRATION","PLC与PLC/控制系统联动","PLC A状态 → 通信交换 → PLC B判断 → 联动动作"),
    ("SYSTEM_INTEGRATION","HMI_HOST_INTEGRATION","PLC与HMI/上位系统联动","操作指令 → PLC接收 → 执行 → 状态反馈 → 界面更新"),
    ("SYSTEM_INTEGRATION","INTERLOCK_RECOVERY","异常联锁与协同恢复","设备异常 → 联锁传播 → 相关响应 → 处理 → 联动恢复"),
    ("LONG_TERM_OPERATION","CONTINUOUS_PRODUCTION","连续生产运行","启动生产 → 周期执行 → 重复业务 → 持续运行"),
    ("LONG_TERM_OPERATION","HIGH_LOAD_OPERATION","高频/高负载持续运行","高频输入/通信 → 高频执行 → 大量数据 → 持续输出"),
    ("LONG_TERM_OPERATION","LONG_TERM_COMMUNICATION","长期通信与设备交互","建链 → 数据交互 → 波动/断连 → 自动恢复"),
    ("LONG_TERM_OPERATION","RESOURCE_STATE_RETENTION","长期资源与状态保持","持续运行 → 资源使用 → 数据/状态累计 → 长周期检查"),
    ("VERSION_MAINTENANCE","INSTALL_ENVIRONMENT","iFA Evolution安装与运行环境构建","安装包 → 软件安装 → 环境配置 → 启动验证"),
    ("VERSION_MAINTENANCE","UPGRADE_DOWNGRADE","软件升级/降级","当前版本 → 升降级 → 环境更新 → 工程打开 → 验证"),
    ("VERSION_MAINTENANCE","PROJECT_MIGRATION","工程版本迁移","老工程 → 新版本转换 → 差异处理 → 编译下载"),
    ("VERSION_MAINTENANCE","FIRMWARE_MAINTENANCE","PLC/固件版本维护","固件升级 → 工程匹配 → 下载 → 功能验证"),
    ("VERSION_MAINTENANCE","DEVICE_CHANGE","硬件/外围设备变更","设备变更 → 配置修改 → 程序适配 → 调试验证"),
    ("VERSION_MAINTENANCE","BACKUP_RESTORE","工程备份与恢复","正常工程 → 备份 → 异常/换机 → 恢复验证"),
    ("VERSION_MAINTENANCE","FUNCTION_CHANGE_RELEASE","程序功能变更与再发布","新需求/问题 → 修改 → 编译 → 下载 → 回归"),
    ("VERSION_MAINTENANCE","VERSION_ROLLBACK","版本回退","新版本异常 → 回退判断 → 恢复旧版 → 验证"),
)

ACTIVITY_DESCRIPTIONS = {
    "POWER_LOSS_RETENTION_RECOVERY": "保证PLC在异常掉电或正常断电后，关键运行数据、参数、计数值和状态能够按预期保持，并在重新上电后正确恢复，避免业务状态丢失或设备行为异常。目标：掉电不丢关键数据，上电后恢复正确、及时、一致。",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenario_taxonomy_version(version_id TEXT PRIMARY KEY,version_no INTEGER NOT NULL UNIQUE,status TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,activated_at TEXT);
CREATE TABLE IF NOT EXISTS scenario_lifecycle(version_id TEXT NOT NULL,lifecycle_code TEXT NOT NULL,label_zh TEXT NOT NULL,description TEXT,enabled INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL,PRIMARY KEY(version_id,lifecycle_code));
CREATE TABLE IF NOT EXISTS scenario_activity(version_id TEXT NOT NULL,activity_code TEXT NOT NULL,lifecycle_code TEXT NOT NULL,label_zh TEXT NOT NULL,chain_text TEXT,description TEXT,enabled INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL,PRIMARY KEY(version_id,activity_code));
CREATE TABLE IF NOT EXISTS quality_scenario(scenario_id TEXT PRIMARY KEY,scenario_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,lifecycle_code TEXT,activity_code TEXT,scenario_chain TEXT,experience_requirement TEXT,concern_points TEXT,quality_attribute TEXT,quality_subcharacteristic TEXT,applicable_boundary TEXT,validation_direction TEXT,measurement_suggestion TEXT,failure_mode TEXT,failure_mechanism TEXT,trigger_conditions TEXT,preconditions TEXT,affected_object TEXT,business_impact TEXT,recovery_method TEXT,status TEXT NOT NULL DEFAULT 'DRAFT',version_no INTEGER NOT NULL DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS quality_scenario_scope(scenario_id TEXT NOT NULL,scope_type TEXT NOT NULL,scope_value TEXT NOT NULL,PRIMARY KEY(scenario_id,scope_type,scope_value));
CREATE TABLE IF NOT EXISTS quality_scenario_generation(generation_id TEXT PRIMARY KEY,product_code TEXT,start_month TEXT,end_month TEXT,source_issue_count INTEGER,candidate_count INTEGER,model_name TEXT,created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS quality_scenario_evidence(scenario_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,evidence_summary TEXT,PRIMARY KEY(scenario_id,knowledge_id));
CREATE TABLE IF NOT EXISTS quality_scenario_duplicate(candidate_id TEXT NOT NULL,existing_id TEXT NOT NULL,similarity REAL NOT NULL,reason TEXT,PRIMARY KEY(candidate_id,existing_id));
CREATE TABLE IF NOT EXISTS quality_scenario_generation_candidate(generation_id TEXT NOT NULL,scenario_id TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(generation_id,scenario_id));
CREATE TABLE IF NOT EXISTS quality_scenario_issue_classification(generation_id TEXT NOT NULL,knowledge_id TEXT NOT NULL,business_issue_id TEXT,status TEXT NOT NULL DEFAULT 'PENDING',scenario_id TEXT,activity_code TEXT,lifecycle_code TEXT,error_message TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(generation_id,knowledge_id));
CREATE TABLE IF NOT EXISTS quality_scenario_industry_variant(variant_id TEXT PRIMARY KEY,scenario_id TEXT NOT NULL,industry TEXT NOT NULL,product_models TEXT,trigger_conditions TEXT,business_impact TEXT,recovery_method TEXT,evidence_count INTEGER NOT NULL DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(scenario_id,industry));
"""


class ScenarioRepository:
    def __init__(self, db_path):
        self.db_path=str(db_path)
        with self.connect() as c:
            c.executescript(SCHEMA)
            scenario_columns={row['name'] for row in c.execute("PRAGMA table_info(quality_scenario)")}
            for name in ('scenario_chain','quality_subcharacteristic','measurement_suggestion','failure_mode','failure_mechanism','trigger_conditions','preconditions','affected_object','business_impact','recovery_method'):
                if name not in scenario_columns:c.execute(f"ALTER TABLE quality_scenario ADD COLUMN {name} TEXT")
            columns={row['name'] for row in c.execute("PRAGMA table_info(quality_scenario_generation)")}
            for name,definition in {'status':"TEXT NOT NULL DEFAULT 'COMPLETED'",'progress_text':"TEXT",'error_message':"TEXT",'finished_at':"TEXT",'processed_count':"INTEGER NOT NULL DEFAULT 0",'classified_count':"INTEGER NOT NULL DEFAULT 0",'review_required_count':"INTEGER NOT NULL DEFAULT 0",'failed_count':"INTEGER NOT NULL DEFAULT 0",'unprocessed_count':"INTEGER NOT NULL DEFAULT 0"}.items():
                if name not in columns:c.execute(f"ALTER TABLE quality_scenario_generation ADD COLUMN {name} {definition}")
            for row in c.execute("SELECT scenario_id,evidence_summary FROM quality_scenario_evidence").fetchall():
                try:generation_id=json.loads(row['evidence_summary'] or '{}').get('generation_id')
                except (TypeError,json.JSONDecodeError):generation_id=''
                if generation_id:c.execute("INSERT OR IGNORE INTO quality_scenario_generation_candidate(generation_id,scenario_id) VALUES(?,?)",(generation_id,row['scenario_id']))
            c.execute("""UPDATE quality_scenario_generation SET status='FAILED',progress_text='历史任务未生成有效候选',error_message='本次AI输出没有形成可用场景，请使用新版本重新生成',finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP)
                       WHERE status='COMPLETED' AND COALESCE(candidate_count,0)=0 AND NOT EXISTS(SELECT 1 FROM quality_scenario_generation_candidate x WHERE x.generation_id=quality_scenario_generation.generation_id)""")
            if not c.execute("SELECT 1 FROM scenario_taxonomy_version").fetchone():
                version_id="STV-1";c.execute("INSERT INTO scenario_taxonomy_version(version_id,version_no,status,activated_at) VALUES(?,1,'ACTIVE',CURRENT_TIMESTAMP)",(version_id,))
                for order,(code,label,description) in enumerate(LIFECYCLES,1):c.execute("INSERT INTO scenario_lifecycle VALUES(?,?,?,?,1,?)",(version_id,code,label,description,order))
                for order,(lifecycle,code,label,chain) in enumerate(ACTIVITIES,1):c.execute("INSERT INTO scenario_activity VALUES(?,?,?,?,?,?,1,?)",(version_id,code,lifecycle,label,chain,ACTIVITY_DESCRIPTIONS.get(code,""),order))
            self._ensure_power_loss_activity(c)
            c.execute("""UPDATE quality_scenario SET scenario_chain=(SELECT a.chain_text FROM scenario_activity a JOIN scenario_taxonomy_version v ON v.version_id=a.version_id WHERE v.status='ACTIVE' AND a.activity_code=quality_scenario.activity_code ORDER BY v.version_no DESC LIMIT 1) WHERE EXISTS(SELECT 1 FROM scenario_activity a JOIN scenario_taxonomy_version v ON v.version_id=a.version_id WHERE v.status='ACTIVE' AND a.activity_code=quality_scenario.activity_code)""")
            self._backfill_context_scopes(c)

    @staticmethod
    def _ensure_power_loss_activity(c):
        code="POWER_LOSS_RETENTION_RECOVERY"
        for version in c.execute("SELECT version_id FROM scenario_taxonomy_version WHERE status IN ('ACTIVE','DRAFT')").fetchall():
            version_id=version['version_id']
            if c.execute("SELECT 1 FROM scenario_activity WHERE version_id=? AND activity_code=?",(version_id,code)).fetchone():continue
            next_row=c.execute("SELECT sort_order FROM scenario_activity WHERE version_id=? AND activity_code='RUNTIME_EXCEPTION_HANDLING'",(version_id,)).fetchone()
            order=next_row[0] if next_row else c.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM scenario_activity WHERE version_id=?",(version_id,)).fetchone()[0]
            c.execute("UPDATE scenario_activity SET sort_order=sort_order+1 WHERE version_id=? AND sort_order>=?",(version_id,order))
            c.execute("INSERT INTO scenario_activity VALUES(?,?,?,?,?,?,1,?)",(version_id,code,"RUNTIME_EXECUTION","掉电数据保持与上电恢复","正常运行 → 关键数据/状态产生 → 掉电 → 数据保持 → 重新上电 → 数据恢复 → 程序继续运行",ACTIVITY_DESCRIPTIONS[code],order))

    @staticmethod
    def _backfill_context_scopes(c):
        aliases={"IPMT":("问题信息_IPMT","IPMT"),"SPDT":("问题信息_SPDT","SPDT"),"PRODUCT_MODEL":("问题信息_产品型号","产品型号"),"INDUSTRY":("问题信息_客户行业","客户行业"),"CUSTOMER_NAME":("问题信息_客户名称","客户名称"),"CUSTOMER_LEVEL":("问题信息_客户分级","客户分级"),"CUSTOMER_STATUS":("问题信息_当前客户状态","问题信息_当前问题状态","问题信息_问题状态","当前客户状态","当前问题状态"),"OCCURRENCE_PHASE":("问题信息_问题发生阶段","问题发生阶段")}
        try:
            rows=c.execute("""SELECT DISTINCT e.scenario_id,m.raw_json FROM quality_scenario_evidence e JOIN issue_material_link l ON l.knowledge_id=e.knowledge_id JOIN source_material m ON m.material_id=l.material_id WHERE m.material_type='ITR_CS'""").fetchall()
        except sqlite3.OperationalError:
            return
        for row in rows:
            try:raw=json.loads(row['raw_json'] or '{}')
            except (TypeError,json.JSONDecodeError):continue
            for kind,names in aliases.items():
                value=next((str(raw.get(name) or '').strip() for name in names if str(raw.get(name) or '').strip()),'')
                if value:c.execute("INSERT OR IGNORE INTO quality_scenario_scope VALUES(?,?,?)",(row['scenario_id'],kind,value))

    def connect(self):
        c=sqlite3.connect(self.db_path);c.row_factory=sqlite3.Row;c.execute("PRAGMA foreign_keys=ON");return c

    def versions(self):
        with self.connect() as c:return [dict(x) for x in c.execute("SELECT * FROM scenario_taxonomy_version ORDER BY version_no DESC")]

    def working_version(self):
        with self.connect() as c:
            row=c.execute("SELECT * FROM scenario_taxonomy_version ORDER BY CASE status WHEN 'DRAFT' THEN 0 ELSE 1 END,version_no DESC LIMIT 1").fetchone();return dict(row)

    def taxonomy(self, version_id=""):
        version=self.working_version() if not version_id else next((x for x in self.versions() if x['version_id']==version_id),None)
        if not version:return None
        with self.connect() as c:
            version['lifecycles']=[dict(x) for x in c.execute("SELECT * FROM scenario_lifecycle WHERE version_id=? ORDER BY sort_order",(version['version_id'],))]
            version['activities']=[dict(x) for x in c.execute("SELECT * FROM scenario_activity WHERE version_id=? ORDER BY sort_order",(version['version_id'],))]
        return version

    def taxonomy_active(self):
        active=next((x for x in self.versions() if x['status']=='ACTIVE'),None)
        return self.taxonomy(active['version_id']) if active else self.taxonomy()

    def create_draft(self):
        with self.connect() as c:
            draft=c.execute("SELECT version_id FROM scenario_taxonomy_version WHERE status='DRAFT' ORDER BY version_no DESC LIMIT 1").fetchone()
            if draft:return draft[0]
            active=c.execute("SELECT version_id,version_no FROM scenario_taxonomy_version WHERE status='ACTIVE' ORDER BY version_no DESC LIMIT 1").fetchone()
            version_id=f"STV-{uuid.uuid4().hex}";version_no=(active['version_no'] if active else 0)+1
            c.execute("INSERT INTO scenario_taxonomy_version(version_id,version_no,status) VALUES(?,?,'DRAFT')",(version_id,version_no))
            if active:
                c.execute("INSERT INTO scenario_lifecycle SELECT ?,lifecycle_code,label_zh,description,enabled,sort_order FROM scenario_lifecycle WHERE version_id=?",(version_id,active['version_id']))
                c.execute("INSERT INTO scenario_activity SELECT ?,activity_code,lifecycle_code,label_zh,chain_text,description,enabled,sort_order FROM scenario_activity WHERE version_id=?",(version_id,active['version_id']))
            return version_id

    def save_lifecycle(self, version_id, code, label, description, enabled=True):
        with self.connect() as c:
            version=c.execute("SELECT status FROM scenario_taxonomy_version WHERE version_id=?",(version_id,)).fetchone()
            if not version or version['status']!='DRAFT':raise ValueError("TAXONOMY_NOT_EDITABLE")
            order=c.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM scenario_lifecycle WHERE version_id=?",(version_id,)).fetchone()[0]
            c.execute("""INSERT INTO scenario_lifecycle VALUES(?,?,?,?,?,?) ON CONFLICT(version_id,lifecycle_code) DO UPDATE SET label_zh=excluded.label_zh,description=excluded.description,enabled=excluded.enabled""",(version_id,code.strip().upper(),label.strip(),description.strip(),int(enabled),order))

    def save_activity(self, version_id, lifecycle_code, code, label, chain, description, enabled=True):
        with self.connect() as c:
            version=c.execute("SELECT status FROM scenario_taxonomy_version WHERE version_id=?",(version_id,)).fetchone()
            if not version or version['status']!='DRAFT':raise ValueError("TAXONOMY_NOT_EDITABLE")
            if not c.execute("SELECT 1 FROM scenario_lifecycle WHERE version_id=? AND lifecycle_code=?",(version_id,lifecycle_code)).fetchone():raise ValueError("LIFECYCLE_NOT_FOUND")
            order=c.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM scenario_activity WHERE version_id=?",(version_id,)).fetchone()[0]
            c.execute("""INSERT INTO scenario_activity VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(version_id,activity_code) DO UPDATE SET lifecycle_code=excluded.lifecycle_code,label_zh=excluded.label_zh,chain_text=excluded.chain_text,description=excluded.description,enabled=excluded.enabled""",(version_id,code.strip().upper(),lifecycle_code,label.strip(),chain.strip(),description.strip(),int(enabled),order))

    def activate(self, version_id):
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM scenario_taxonomy_version WHERE version_id=? AND status='DRAFT'",(version_id,)).fetchone():raise ValueError("DRAFT_NOT_FOUND")
            c.execute("UPDATE scenario_taxonomy_version SET status='RETIRED' WHERE status='ACTIVE'")
            c.execute("UPDATE scenario_taxonomy_version SET status='ACTIVE',activated_at=CURRENT_TIMESTAMP WHERE version_id=?",(version_id,))
            c.execute("""UPDATE quality_scenario SET scenario_chain=(SELECT chain_text FROM scenario_activity WHERE version_id=? AND activity_code=quality_scenario.activity_code) WHERE EXISTS(SELECT 1 FROM scenario_activity WHERE version_id=? AND activity_code=quality_scenario.activity_code)""",(version_id,version_id))

    def scope_options(self):
        values={key:set() for key in ("IPMT","SPDT","PRODUCT_MODEL","INDUSTRY","CUSTOMER_NAME","CUSTOMER_LEVEL","CUSTOMER_STATUS","OCCURRENCE_PHASE")}
        aliases={"IPMT":("问题信息_IPMT","IPMT"),"SPDT":("问题信息_SPDT","SPDT"),"PRODUCT_MODEL":("问题信息_产品型号","产品型号"),"INDUSTRY":("问题信息_客户行业","客户行业"),"CUSTOMER_NAME":("问题信息_客户名称","客户名称"),"CUSTOMER_LEVEL":("问题信息_客户分级","客户分级"),"CUSTOMER_STATUS":("问题信息_当前客户状态","问题信息_当前问题状态","问题信息_问题状态","当前客户状态","当前问题状态"),"OCCURRENCE_PHASE":("问题信息_问题发生阶段","问题发生阶段")}
        with self.connect() as c:
            try:rows=c.execute("SELECT raw_json FROM source_material WHERE material_type='ITR_CS'").fetchall()
            except sqlite3.OperationalError:rows=[]
            for row in rows:
                raw=json.loads(row[0] or "{}")
                for kind,names in aliases.items():
                    for name in names:
                        value=str(raw.get(name) or "").strip()
                        if value:values[kind].add(value);break
            for row in c.execute("SELECT scope_type,scope_value FROM quality_scenario_scope"):
                values.setdefault(row['scope_type'],set()).add(row['scope_value'])
        return {key:sorted(items) for key,items in values.items()}

    def scenarios(self, *, ipmt="", spdt="", product_model="", industry="", customer_name="", q="", status="", generation_id=""):
        with self.connect() as c:
            rows=[dict(x) for x in c.execute("SELECT * FROM quality_scenario ORDER BY updated_at DESC")]
            scopes=c.execute("SELECT * FROM quality_scenario_scope").fetchall()
            generated={x[0] for x in c.execute("SELECT scenario_id FROM quality_scenario_generation_candidate WHERE generation_id=?",(generation_id,))} if generation_id else set()
        by_id={}
        for row in scopes:by_id.setdefault(row['scenario_id'],{}).setdefault(row['scope_type'],[]).append(row['scope_value'])
        for item in rows:item['scopes']=by_id.get(item['scenario_id'],{})
        def matches(item):
            s=item['scopes']
            return (not generation_id or item['scenario_id'] in generated) and (not q or q.lower() in (item['name']+' '+item['scenario_code']).lower()) and (not status or item['status']==status) and (not ipmt or ipmt in s.get('IPMT',[])) and (not spdt or spdt in s.get('SPDT',[])) and (not product_model or product_model in s.get('PRODUCT_MODEL',[])) and (not industry or industry in s.get('INDUSTRY',[])) and (not customer_name or customer_name in s.get('CUSTOMER_NAME',[]))
        return [item for item in rows if matches(item)]

    def scenario(self, scenario_id):
        item=next(iter(self.scenarios()),None) if not scenario_id else next((x for x in self.scenarios() if x['scenario_id']==scenario_id),None)
        if item:
            with self.connect() as c:item['evidence']=[dict(x) for x in c.execute("SELECT * FROM quality_scenario_evidence WHERE scenario_id=?",(item['scenario_id'],))]
            for evidence in item['evidence']:
                try:evidence['meta']=json.loads(evidence.get('evidence_summary') or '{}')
                except (TypeError,json.JSONDecodeError):evidence['meta']={'summary':evidence.get('evidence_summary')}
            with self.connect() as c:item['duplicates']=[dict(x) for x in c.execute("SELECT d.*,s.name,s.status FROM quality_scenario_duplicate d JOIN quality_scenario s ON s.scenario_id=d.existing_id WHERE d.candidate_id=? ORDER BY d.similarity DESC",(item['scenario_id'],))]
            with self.connect() as c:item['industry_variants']=[dict(x) for x in c.execute("SELECT * FROM quality_scenario_industry_variant WHERE scenario_id=? ORDER BY evidence_count DESC,industry",(item['scenario_id'],))]
            for variant in item['industry_variants']:
                try:variant['product_model_values']=json.loads(variant.get('product_models') or '[]')
                except (TypeError,json.JSONDecodeError):variant['product_model_values']=[]
        return item

    def create_generation(self,generation_id,product,start,end,source_count,created_by):
        with self.connect() as c:c.execute("INSERT INTO quality_scenario_generation(generation_id,product_code,start_month,end_month,source_issue_count,candidate_count,model_name,created_by,status,progress_text,unprocessed_count) VALUES(?,?,?,?,?,0,'',?,'QUEUED','等待开始',?)",(generation_id,product,start,end,source_count,created_by,source_count))

    def initialize_issue_classifications(self,generation_id,records):
        with self.connect() as c:
            for row in records:
                c.execute("INSERT OR IGNORE INTO quality_scenario_issue_classification(generation_id,knowledge_id,business_issue_id,status) VALUES(?,?,?,'PENDING')",(generation_id,row['knowledge_id'],row.get('business_issue_id')))
        self.refresh_generation_coverage(generation_id)

    def mark_issue_classification(self,generation_id,knowledge_id,status,*,scenario_id='',activity_code='',lifecycle_code='',error_message=''):
        with self.connect() as c:
            c.execute("""UPDATE quality_scenario_issue_classification SET status=?,scenario_id=?,activity_code=?,lifecycle_code=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE generation_id=? AND knowledge_id=?""",(status,scenario_id or None,activity_code or None,lifecycle_code or None,error_message or None,generation_id,knowledge_id))

    def refresh_generation_coverage(self,generation_id):
        with self.connect() as c:
            counts={row['status']:row['n'] for row in c.execute("SELECT status,COUNT(*) n FROM quality_scenario_issue_classification WHERE generation_id=? GROUP BY status",(generation_id,))}
            total=sum(counts.values());classified=counts.get('CLASSIFIED',0);review=counts.get('REVIEW_REQUIRED',0);failed=counts.get('FAILED',0);unprocessed=counts.get('PENDING',0)
            c.execute("UPDATE quality_scenario_generation SET processed_count=?,classified_count=?,review_required_count=?,failed_count=?,unprocessed_count=? WHERE generation_id=?",(classified+review+failed,classified,review,failed,unprocessed,generation_id))
        return {'total':total,'processed_count':classified+review+failed,'classified_count':classified,'review_required_count':review,'failed_count':failed,'unprocessed_count':unprocessed}

    def issue_classifications(self,generation_id):
        with self.connect() as c:return [dict(x) for x in c.execute("SELECT * FROM quality_scenario_issue_classification WHERE generation_id=? ORDER BY knowledge_id",(generation_id,))]

    def update_generation(self,generation_id,**values):
        allowed={'status','progress_text','error_message','candidate_count','model_name','processed_count','classified_count','review_required_count','failed_count','unprocessed_count'};data={k:v for k,v in values.items() if k in allowed}
        if not data:return
        assignments=','.join(f'{key}=?' for key in data)
        finished=",finished_at=CURRENT_TIMESTAMP" if data.get('status') in {'COMPLETED','PARTIAL','FAILED'} else ''
        with self.connect() as c:c.execute(f"UPDATE quality_scenario_generation SET {assignments}{finished} WHERE generation_id=?",(*data.values(),generation_id))

    def generation(self,generation_id):
        with self.connect() as c:
            row=c.execute("SELECT * FROM quality_scenario_generation WHERE generation_id=?",(generation_id,)).fetchone();return dict(row) if row else None

    def generations(self):
        with self.connect() as c:return [dict(x) for x in c.execute("SELECT g.*,(SELECT COUNT(*) FROM quality_scenario_generation_candidate x WHERE x.generation_id=g.generation_id) linked_candidate_count FROM quality_scenario_generation g ORDER BY g.created_at DESC LIMIT 30")]

    def insights(self, *, status=''):
        items=self.scenarios(status=status)
        taxonomy=self.taxonomy_active() or {'activities':[],'lifecycles':[]}
        activity_labels={x['activity_code']:x['label_zh'] for x in taxonomy['activities']}
        lifecycle_labels={x['lifecycle_code']:x['label_zh'] for x in taxonomy['lifecycles']}
        activity_rows={};industry_rows={}
        for item in items:
            industries=item.get('scopes',{}).get('INDUSTRY') or ['未提供行业']
            evidence_count=len((self.scenario(item['scenario_id']) or {}).get('evidence',[]))
            activity=item.get('activity_code') or 'UNCLASSIFIED'
            a=activity_rows.setdefault(activity,{'activity_code':activity,'activity_label':activity_labels.get(activity,activity),'lifecycle_label':lifecycle_labels.get(item.get('lifecycle_code'),item.get('lifecycle_code') or '未分类'),'scenario_ids':set(),'issue_count':0,'industries':{}})
            a['scenario_ids'].add(item['scenario_id']);a['issue_count']+=evidence_count
            for industry in industries:
                a['industries'][industry]=a['industries'].get(industry,0)+evidence_count
                i=industry_rows.setdefault(industry,{'industry':industry,'scenario_ids':set(),'issue_count':0,'activities':{}})
                i['scenario_ids'].add(item['scenario_id']);i['issue_count']+=evidence_count;i['activities'][activity_labels.get(activity,activity)]=i['activities'].get(activity_labels.get(activity,activity),0)+evidence_count
        def finish(rows):
            result=[]
            for row in rows.values():
                row['scenario_count']=len(row.pop('scenario_ids'));result.append(row)
            return sorted(result,key=lambda x:(-x['issue_count'],str(x.get('activity_label') or x.get('industry'))))
        return {'activity_rows':finish(activity_rows),'industry_rows':finish(industry_rows),'scenario_count':len(items),'issue_count':sum(len((self.scenario(x['scenario_id']) or {}).get('evidence',[])) for x in items)}

    def save_generated_candidate(self,code,item,scopes,generation_id,product,start,end,model):
        payload={**item,'scenario_code':code,'status':'IN_REVIEW','applicable_boundary':item.get('applicable_boundary') or f'{product}；{start}—{end}'}
        scenario_id=self.save_scenario('',payload,scopes)
        summary=json.dumps({'generation_id':generation_id,'product':product,'period':f'{start}—{end}','model':model,'summary':item.get('evidence_summary'),'confidence':item.get('confidence'),'questions':item.get('confirmation_questions',[])},ensure_ascii=False)
        with self.connect() as c:
            for knowledge_id in item.get('evidence_issue_ids',[]):c.execute("INSERT OR IGNORE INTO quality_scenario_evidence VALUES(?,?,?)",(scenario_id,knowledge_id,summary))
            c.execute("INSERT OR IGNORE INTO quality_scenario_generation_candidate(generation_id,scenario_id) VALUES(?,?)",(generation_id,scenario_id))
        self._detect_duplicates(scenario_id)
        return scenario_id

    def save_industry_variants(self,scenario_id,variants):
        with self.connect() as c:
            c.execute("DELETE FROM quality_scenario_industry_variant WHERE scenario_id=?",(scenario_id,))
            for row in variants:
                c.execute("""INSERT INTO quality_scenario_industry_variant(variant_id,scenario_id,industry,product_models,trigger_conditions,business_impact,recovery_method,evidence_count) VALUES(?,?,?,?,?,?,?,?)""",(f"QSV-{uuid.uuid4().hex}",scenario_id,row['industry'],json.dumps(row.get('product_models',[]),ensure_ascii=False),row.get('trigger_conditions',''),row.get('business_impact',''),row.get('recovery_method',''),int(row.get('evidence_count') or 0)))

    @staticmethod
    def _grams(value):
        text=''.join(ch.lower() for ch in str(value or '') if ch.isalnum())
        return {text[i:i+2] for i in range(max(1,len(text)-1))} if text else set()

    def _detect_duplicates(self,candidate_id):
        candidate=self.scenario(candidate_id)
        if not candidate:return
        a=self._grams(candidate['name']+' '+candidate.get('concern_points','')+' '+candidate.get('quality_attribute',''))
        with self.connect() as c:
            c.execute("DELETE FROM quality_scenario_duplicate WHERE candidate_id=?",(candidate_id,))
            rows=c.execute("SELECT * FROM quality_scenario WHERE scenario_id<>? AND status IN ('IN_REVIEW','PUBLISHED')",(candidate_id,)).fetchall()
            for row in rows:
                other=dict(row);b=self._grams(other['name']+' '+other.get('concern_points','')+' '+other.get('quality_attribute',''))
                lexical=len(a&b)/len(a|b) if a|b else 0
                same_activity=bool(candidate.get('activity_code') and candidate.get('activity_code')==other.get('activity_code'))
                score=min(1,lexical+(0.25 if same_activity else 0))
                if score>=0.55:c.execute("INSERT INTO quality_scenario_duplicate VALUES(?,?,?,?)",(candidate_id,other['scenario_id'],round(score,3),'业务活动一致且场景语义相近' if same_activity else '场景语义相近'))

    def save_scenario(self, scenario_id, payload, scopes):
        scenario_id=scenario_id or f"QSC-{uuid.uuid4().hex}"
        status=payload.get('status','DRAFT')
        if status not in {'DRAFT','IN_REVIEW','PUBLISHED','RETIRED'}:raise ValueError('INVALID_SCENARIO_STATUS')
        with self.connect() as c:
            existing=c.execute("SELECT version_no FROM quality_scenario WHERE scenario_id=?",(scenario_id,)).fetchone();version=(existing[0]+1 if existing else 1)
            chain=c.execute("""SELECT a.chain_text FROM scenario_activity a JOIN scenario_taxonomy_version v ON v.version_id=a.version_id WHERE v.status='ACTIVE' AND a.activity_code=? ORDER BY v.version_no DESC LIMIT 1""",(payload.get('activity_code',''),)).fetchone()
            scenario_chain=(chain[0] if chain else '') or ''
            c.execute("""INSERT INTO quality_scenario(scenario_id,scenario_code,name,lifecycle_code,activity_code,scenario_chain,experience_requirement,concern_points,quality_attribute,quality_subcharacteristic,applicable_boundary,validation_direction,measurement_suggestion,failure_mode,failure_mechanism,trigger_conditions,preconditions,affected_object,business_impact,recovery_method,status,version_no,created_at,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
             ON CONFLICT(scenario_id) DO UPDATE SET name=excluded.name,lifecycle_code=excluded.lifecycle_code,activity_code=excluded.activity_code,scenario_chain=excluded.scenario_chain,experience_requirement=excluded.experience_requirement,concern_points=excluded.concern_points,quality_attribute=excluded.quality_attribute,quality_subcharacteristic=excluded.quality_subcharacteristic,applicable_boundary=excluded.applicable_boundary,validation_direction=excluded.validation_direction,measurement_suggestion=excluded.measurement_suggestion,failure_mode=excluded.failure_mode,failure_mechanism=excluded.failure_mechanism,trigger_conditions=excluded.trigger_conditions,preconditions=excluded.preconditions,affected_object=excluded.affected_object,business_impact=excluded.business_impact,recovery_method=excluded.recovery_method,status=excluded.status,version_no=excluded.version_no,updated_at=CURRENT_TIMESTAMP""",
             (scenario_id,payload['scenario_code'].strip(),payload['name'].strip(),payload.get('lifecycle_code',''),payload.get('activity_code',''),scenario_chain,payload.get('experience_requirement',''),payload.get('concern_points',''),payload.get('quality_attribute',''),payload.get('quality_subcharacteristic',''),payload.get('applicable_boundary',''),payload.get('validation_direction',''),payload.get('measurement_suggestion',''),payload.get('failure_mode',''),payload.get('failure_mechanism',''),payload.get('trigger_conditions',''),payload.get('preconditions',''),payload.get('affected_object',''),payload.get('business_impact',''),payload.get('recovery_method',''),status,version))
            c.execute("DELETE FROM quality_scenario_scope WHERE scenario_id=?",(scenario_id,))
            for kind,items in scopes.items():
                for value in items:
                    if value.strip():c.execute("INSERT OR IGNORE INTO quality_scenario_scope VALUES(?,?,?)",(scenario_id,kind,value.strip()))
        return scenario_id

    def delete_scenario(self, scenario_id):
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM quality_scenario WHERE scenario_id=?",(scenario_id,)).fetchone():raise KeyError(scenario_id)
            generation_ids=[row[0] for row in c.execute("SELECT generation_id FROM quality_scenario_generation_candidate WHERE scenario_id=?",(scenario_id,))]
            c.execute("DELETE FROM quality_scenario_duplicate WHERE candidate_id=? OR existing_id=?",(scenario_id,scenario_id))
            c.execute("DELETE FROM quality_scenario_evidence WHERE scenario_id=?",(scenario_id,))
            c.execute("DELETE FROM quality_scenario_industry_variant WHERE scenario_id=?",(scenario_id,))
            c.execute("DELETE FROM quality_scenario_scope WHERE scenario_id=?",(scenario_id,))
            c.execute("DELETE FROM quality_scenario_generation_candidate WHERE scenario_id=?",(scenario_id,))
            c.execute("DELETE FROM quality_scenario WHERE scenario_id=?",(scenario_id,))
            for generation_id in generation_ids:
                c.execute("UPDATE quality_scenario_generation SET candidate_count=(SELECT COUNT(*) FROM quality_scenario_generation_candidate WHERE generation_id=?) WHERE generation_id=?",(generation_id,generation_id))
