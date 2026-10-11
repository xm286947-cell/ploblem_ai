"""Stage5 isolated HTTP review/audit, controlled permissions and no old DB writes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.qs_next_review import ReviewStore, ReviewError, create_review_router

SCHEMA = """
CREATE TABLE data_group(group_id TEXT PRIMARY KEY,group_code TEXT,material_type TEXT);
CREATE TABLE source_material(
    material_id TEXT PRIMARY KEY,group_id TEXT,material_type TEXT,
    business_key TEXT,canonical_itr TEXT,version_no INTEGER,
    source_hash TEXT,raw_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE issue_material_link(
    link_id TEXT PRIMARY KEY,knowledge_id TEXT,material_id TEXT,link_status TEXT);
"""
BASE="/api/v2/qs-next/review/v1"


class TestStage5Review(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src=Path(self.tmp.name)/"protected_original.sqlite3"
        self.store_path=Path(self.tmp.name)/"stage5_review.sqlite3"
        with sqlite3.connect(self.src) as db:
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO data_group VALUES(?,?,?)",[
                ("CS","ITR-CS","ITR_CS"),("MT","ESCAPE","ESCAPE_ANALYSIS"),
                ("AS","SW-OPS","SOFTWARE_OPERATION")])
            self.insert(db,"CS-1","ITR_CS","ITR-001",{
                "问题信息_问题领域":"软件",
                "lifecycle_code":"RUNTIME_EXECUTION",
                "activity_code":"TASK_CYCLE_EXECUTION",
                "用户类型":"现场操作人员",
                "触发条件":"连续运行之后",
                "客户质量体验要求":"关键生产任务必须完成",
                "测试验证方向":"长时间执行验证",
                "技术根因分析与纠正_产品层级软件失效模式":"任务周期异常",
                "产品编码":"PLC",
            })
            self.insert(db,"MT-1","ESCAPE_ANALYSIS","ITR-001",{
                "测试验证方向":"异常时序专项验证",
                "测试漏测原因":"没有覆盖这一时序",
            })
            self.insert(db,"AS-1","SOFTWARE_OPERATION","ITR-001",{
                "业务影响":"KPI不应成为正式证据",
            })
            self.insert(db,"CS-M","ITR_CS","ITR-M",{
                "问题信息_问题领域":"机械",
                "技术根因分析与纠正_产品层级机械失效模式":"导轨卡滞",
            })
            self.insert(db,"AS-X","SOFTWARE_OPERATION","ITR-X",{})
            for mid in ("CS-1","MT-1","AS-1"):
                db.execute("INSERT INTO issue_material_link VALUES(?,?,?,?)",
                           ("L-"+mid,"K-1",mid,"LINKED"))
        self.original_hash=hashlib.sha256(self.src.read_bytes()).hexdigest()

    @staticmethod
    def insert(db, mid, kind, itr, raw):
        group={"ITR_CS":"CS","ESCAPE_ANALYSIS":"MT",
               "SOFTWARE_OPERATION":"AS"}[kind]
        blob=json.dumps(raw,ensure_ascii=False)
        db.execute("""INSERT INTO source_material
           (material_id,group_id,material_type,business_key,canonical_itr,
           version_no,source_hash,raw_json) VALUES(?,?,?,?,?,?,?,?)""",
           (mid,group,kind,mid,itr,1,hashlib.sha256(blob.encode()).hexdigest(),blob))

    def client(self, *, security=True, taxonomy=False, permissions=True):
        app=FastAPI()
        kwargs=(
            {"trusted_actor":lambda _r:"quality-reviewer@company",
             "authorize":lambda _a,_action,_subject: permissions,
             "taxonomy_verified":lambda _candidate: taxonomy}
            if security else {})
        app.include_router(create_review_router(self.src,self.store_path,**kwargs))
        return TestClient(app)

    def start(self, c, wb="cs", mid="CS-1"):
        return c.post(f"{BASE}/start/{wb}/{mid}")

    @staticmethod
    def revise(c, rid, rev, changes, *, conflicts=None, reason="人工核实现场记录"):
        return c.post(f"{BASE}/{rid}/revise", json={
            "expected_revision":rev,"changes":changes,"reason":reason,
            "resolved_conflicts":conflicts or {}})

    @staticmethod
    def decide(c, rid, rev, decision="CONFIRM", reason="完成质量审查"):
        return c.post(f"{BASE}/{rid}/decide",json={
            "expected_revision":rev,"decision":decision,"reason":reason})

    def test_default_router_does_not_allow_unauthenticated_mutation(self):
        c=self.client(security=False)
        self.assertEqual(403,self.start(c).status_code)
        self.assertFalse(self.store_path == self.src)

    def test_denied_role_cannot_create_or_read(self):
        c=self.client(permissions=False)
        self.assertEqual(403,self.start(c).status_code)
        self.assertEqual(403,c.get(f"{BASE}/a-review").status_code)

    def test_review_creation_persists_only_to_new_database(self):
        c=self.client()
        r=self.start(c)
        self.assertEqual(200,r.status_code,r.text)
        s=r.json()
        self.assertEqual("IN_REVIEW",s["status"])
        self.assertEqual(1,s["revision"])
        self.assertFalse(s["publish_ready"])
        self.assertEqual(["SOFTWARE"],s["problem_domains"])
        self.assertEqual(["validation_direction"],s["unresolved_conflicts"])
        self.assertEqual(self.original_hash,hashlib.sha256(self.src.read_bytes()).hexdigest())
        self.assertTrue(self.store_path.is_file())
        self.assertEqual(s["review_id"],self.start(c).json()["review_id"])
        self.assertEqual(1,self.start(c).json()["revision"])
        self.assertEqual(1,len(c.get(f'{BASE}/{s["review_id"]}/audit').json()))

    def test_review_revision_and_conflict_cannot_auto_confirm(self):
        c=self.client(taxonomy=True)
        start=self.start(c).json();rid=start["review_id"]
        self.assertEqual(409,self.decide(c,rid,1).status_code)
        self.assertEqual("UNRESOLVED_SOURCE_CONFLICTS",self.decide(c,rid,1).json()["detail"])
        noresolve=self.revise(c,rid,1,{"validation_direction":"冲突中的候选值"})
        self.assertEqual(200,noresolve.status_code)
        self.assertEqual(2,noresolve.json()["revision"])
        self.assertIn("validation_direction",noresolve.json()["unresolved_conflicts"])
        self.assertEqual(409,self.decide(c,rid,2).status_code)
        patched=self.revise(c,rid,2,
            {"validation_direction":"现场确认采用双场景验证"},
            conflicts={"validation_direction":"两份证据冲突，经评审核准双场景"})
        self.assertEqual(200,patched.status_code)
        self.assertEqual([],patched.json()["unresolved_conflicts"])
        self.assertEqual("HUMAN_CONFIRMED",
                         patched.json()["human_edits"]["validation_direction"]["provenance"])
        final=self.decide(c,rid,3)
        self.assertEqual(200,final.status_code,final.text)
        self.assertEqual("CONFIRMED",final.json()["status"])
        self.assertFalse(final.json()["publish_ready"])
        self.assertEqual(4,final.json()["revision"])
        self.assertEqual(409,self.revise(c,rid,4,{"user_type":"其他"}).status_code)
        history=c.get(f"{BASE}/{rid}/audit").json()
        self.assertEqual(["START","REVISE","REVISE","CONFIRM"],[x["action"] for x in history])
        self.assertEqual([1,2,3,4],[x["revision"] for x in history])
        self.assertTrue(all(x["actor"]=="quality-reviewer@company" for x in history))
        self.assertEqual(self.original_hash,hashlib.sha256(self.src.read_bytes()).hexdigest())

    def test_taxonomy_gate_is_closed_by_default(self):
        c=self.client(taxonomy=False)
        s=self.start(c).json();rid=s["review_id"]
        p=self.revise(c,rid,1,{"validation_direction":"现场确认值"},
            conflicts={"validation_direction":"有证据完成核对"})
        self.assertEqual(200,p.status_code)
        r=self.decide(c,rid,2)
        self.assertEqual(409,r.status_code)
        self.assertEqual("CONTROLLED_TAXONOMY_NOT_VERIFIED",r.json()["detail"])

    def test_conflict_resolution_needs_explicit_review_value(self):
        c=self.client()
        rid=self.start(c).json()["review_id"]
        r=self.revise(c,rid,1,{},conflicts={"validation_direction":"解释原因"})
        self.assertEqual(400,r.status_code)
        self.assertEqual("CONFLICT_RESOLUTION_REQUIRES_EXPLICIT_VALUE",r.json()["detail"])

    def test_optimistic_lock_prevents_lost_updates(self):
        c=self.client()
        rid=self.start(c).json()["review_id"]
        self.assertEqual(200,self.revise(c,rid,1,{"user_type":"维护人员"}).status_code)
        r=self.revise(c,rid,1,{"user_type":"其他人员"})
        self.assertEqual(409,r.status_code)
        self.assertEqual("REVIEW_REVISION_CONFLICT",r.json()["detail"])

    def test_missing_required_fields_block_confirmation(self):
        c=self.client(taxonomy=True)
        rid=self.start(c,"cs","CS-M").json()["review_id"]
        r=self.decide(c,rid,1)
        self.assertEqual(409,r.status_code)
        self.assertEqual("REQUIRED_SCENARIO_FIELDS_MISSING",r.json()["detail"])
        changes={
            "lifecycle_code":"SYSTEM_INTEGRATION",
            "activity_code":"INTERLOCK_RECOVERY",
            "user_type":"维护工程师",
            "experience_requirement":"机构不卡滞",
            "trigger_conditions":"持续摩擦时",
            "validation_direction":"机械压力循环验证",
        }
        self.assertEqual(200,self.revise(c,rid,1,changes).status_code)
        r=self.decide(c,rid,2)
        self.assertEqual(200,r.status_code,r.text)
        self.assertEqual(["MECHANICAL"],r.json()["problem_domains"])

    def test_reject_is_terminal_and_audited(self):
        c=self.client()
        rid=self.start(c,"cs","CS-M").json()["review_id"]
        r=self.decide(c,rid,1,"REJECT","材料未覆盖关键使用条件")
        self.assertEqual("REJECTED",r.json()["status"])
        self.assertEqual(409,self.decide(c,rid,2,"CONFIRM").status_code)
        self.assertEqual("REJECT",c.get(f"{BASE}/{rid}/audit").json()[-1]["action"])

    def test_no_formal_source_never_creates_review(self):
        c=self.client()
        r=self.start(c,"software-assessment","AS-X")
        self.assertEqual(409,r.status_code)
        self.assertEqual("FORMAL_SOURCE_REQUIRED",r.json()["detail"])
        with sqlite3.connect(self.store_path) as db:
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM qs_next_review").fetchone()[0])

    def test_source_snapshot_change_prevents_subsequent_review_write(self):
        c=self.client()
        rid=self.start(c).json()["review_id"]
        with sqlite3.connect(self.src) as db:
            db.execute("UPDATE source_material SET source_hash=? WHERE material_id='CS-1'",
                       ("f"*64,))
        r=self.revise(c,rid,1,{"user_type":"现场人员"})
        self.assertEqual(409,r.status_code)
        self.assertEqual("SOURCE_SNAPSHOT_CHANGED",r.json()["detail"])

    def test_review_and_source_database_cannot_be_the_same(self):
        with self.assertRaises(ReviewError) as c:
            ReviewStore(self.src,self.src)
        self.assertEqual("REVIEW_STORE_MUST_BE_ISOLATED",c.exception.code)

    def test_patch_cannot_set_arbitrary_fields_or_actor(self):
        c=self.client()
        rid=self.start(c).json()["review_id"]
        r=self.revise(c,rid,1,{"publish_ready":"true"})
        self.assertEqual(400,r.status_code)
        self.assertEqual("UNKNOWN_REVIEW_FIELD",r.json()["detail"])
        r=self.revise(c,rid,1,{"user_type":"假冒"},reason="")
        self.assertEqual(400,r.status_code)

    def test_software_assessment_does_not_store_kpi_as_fact(self):
        c=self.client()
        r=self.start(c,"software-assessment","AS-1")
        self.assertEqual(200,r.status_code,r.text)
        self.assertNotIn("KPI不应成为正式证据",json.dumps(r.json(),ensure_ascii=False))
        self.assertEqual("IN_REVIEW",r.json()["status"])


if __name__=="__main__":
    unittest.main()
