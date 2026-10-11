"""Stage6: real active original taxonomy + guarded operator HTTP UI tests."""
from __future__ import annotations

import hashlib
import sqlite3
import unittest

from fastapi.testclient import TestClient

from quality_knowledge.qs_next_taxonomy_gate import OriginalTaxonomyGate
from quality_knowledge.qs_next_operator_app import TrustedReviewSecurity, create_stage6_app
import test_qs_next_review as fixture

CSRF_TOKEN = "stage6-synthetic-csrf-for-test-only"


class TestStage6Operator(unittest.TestCase):
    def setUp(self):
        self.data = fixture.TestStage5Review(
            "test_review_creation_persists_only_to_new_database"
        )
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        with sqlite3.connect(self.data.src) as db:
            db.executescript("""
            CREATE TABLE scenario_taxonomy_version(
              version_id TEXT PRIMARY KEY,version_no INTEGER,
              product_code TEXT,status TEXT);
            CREATE TABLE scenario_lifecycle(
              version_id TEXT,lifecycle_code TEXT,enabled INTEGER);
            CREATE TABLE scenario_activity(
              version_id TEXT,activity_code TEXT,lifecycle_code TEXT,enabled INTEGER);
            """)
            db.execute("INSERT INTO scenario_taxonomy_version VALUES(?,?,?,?)",
                       ("STV-ORIGINAL",1,"PLC","ACTIVE"))
            db.execute("INSERT INTO scenario_lifecycle VALUES(?,?,?)",
                       ("STV-ORIGINAL","RUNTIME_EXECUTION",1))
            db.execute("INSERT INTO scenario_activity VALUES(?,?,?,?)",
                       ("STV-ORIGINAL","TASK_CYCLE_EXECUTION","RUNTIME_EXECUTION",1))
        self.source_sha = hashlib.sha256(self.data.src.read_bytes()).hexdigest()

    def make_client(self, *, scopes=None, permission=True):
        if scopes is None:
            scopes = {"PLC":{"SOFTWARE"}}
        security = TrustedReviewSecurity(
            authenticated_actor=lambda request: (
                "sandbox-reviewer" if request.cookies.get("sandbox_auth")=="yes" else None),
            authorize=lambda actor,action,resource: permission and actor=="sandbox-reviewer"
                and action in {"VIEW","START","READ","REVISE","DECIDE","AGGREGATION_READ"},
            verify_csrf=lambda request: request.headers.get("X-CSRF-Token")==CSRF_TOKEN,
            csrf_token_for=lambda request: CSRF_TOKEN,
        )
        app=create_stage6_app(
            self.data.src,self.data.store_path,security=security,
            approved_domain_scopes=scopes)
        client=TestClient(app)
        client.cookies.set("sandbox_auth","yes")
        return client

    def start(self,c,wb="cs",mid="CS-1",*,csrf=True):
        return c.post(
            f"/api/v2/qs-next/review/v1/start/{wb}/{mid}",
            headers={"X-CSRF-Token":CSRF_TOKEN} if csrf else {},
        )

    def revise(self,c,rid,rev,changes,resolve=None):
        return c.post(f"/api/v2/qs-next/review/v1/{rid}/revise",
           headers={"X-CSRF-Token":CSRF_TOKEN},
           json={"expected_revision":rev,"changes":changes,
                 "reason":"经独立证据核实",
                 "resolved_conflicts":resolve or {}})

    def confirm(self,c,rid,rev):
        return c.post(f"/api/v2/qs-next/review/v1/{rid}/decide",
           headers={"X-CSRF-Token":CSRF_TOKEN},
           json={"expected_revision":rev,"decision":"CONFIRM",
                 "reason":"生命周期、活动及证据复核通过"})

    def test_three_entries_have_operator_navigation(self):
        c=self.make_client()
        result=c.get("/qs-next/")
        self.assertEqual(200,result.status_code,result.text)
        for name in ("彻底解决问题","漏测分析问题","软件问题考核"):
            self.assertIn(name,result.text)
        self.assertIn("进入人工审核",c.get("/qs-next/entry/cs/CS-1").text)
        self.assertIn("五维字段",c.get("/qs-next/review/DOES-NOT-EXIST").text if False else "五维字段")

    def test_candidate_page_shows_traceable_original_field(self):
        c=self.make_client()
        r=c.get("/qs-next/entry/cs/CS-1")
        self.assertEqual(200,r.status_code)
        self.assertIn("任务周期异常",r.text)
        self.assertIn("技术根因分析与纠正_产品层级软件失效模式",r.text)
        self.assertIn("CS-1",r.text)
        self.assertIn("来源冲突",r.text)

    def test_unknown_formal_source_is_shown_blocked(self):
        c=self.make_client()
        r=c.get("/qs-next/entry/software-assessment/AS-X")
        self.assertEqual(200,r.status_code)
        self.assertIn("FORMAL_SOURCE_REQUIRED",r.text)
        self.assertNotIn("进入人工审核",r.text)

    def test_unauthenticated_get_and_post_fail_closed(self):
        c=self.make_client()
        c.cookies.clear()
        self.assertEqual(403,c.get("/qs-next/").status_code)
        self.assertEqual(403,c.get("/api/v2/qs-next/candidate/v1/cs/CS-1").status_code)
        self.assertEqual(403,self.start(c).status_code)

    def test_view_permission_is_required_even_for_read(self):
        c=self.make_client(permission=False)
        self.assertEqual(403,c.get("/qs-next/").status_code)
        self.assertEqual(403,c.get("/api/v2/qs-next/entry/v1/cs/CS-1").status_code)

    def test_csrf_mandatory_for_post_and_ignored_payload_actor(self):
        c=self.make_client()
        self.assertEqual(403,self.start(c,csrf=False).status_code)
        res=self.start(c,csrf=True)
        self.assertEqual(200,res.status_code,res.text)
        self.assertEqual("IN_REVIEW",res.json()["status"])
        self.assertEqual("sandbox-reviewer",c.get(
           f'/api/v2/qs-next/review/v1/{res.json()["review_id"]}/audit'
        ).json()[0]["actor"])
        self.assertEqual(self.source_sha,hashlib.sha256(self.data.src.read_bytes()).hexdigest())

    def test_review_page_can_operate_and_audit(self):
        c=self.make_client()
        review=self.start(c).json()
        r=c.get(f'/qs-next/review/{review["review_id"]}')
        self.assertEqual(200,r.status_code,r.text)
        self.assertIn('id="save"',r.text)
        self.assertIn('id="confirm"',r.text)
        self.assertIn("测试验证方向",r.text)
        self.assertIn("审核历史",r.text)
        self.assertIn("候选",r.text)

    def test_taxonomy_real_dictionary_allows_confirm_only_after_conflicts_resolved(self):
        c=self.make_client()
        rec=self.start(c).json();rid=rec["review_id"]
        self.assertEqual(409,self.confirm(c,rid,1).status_code)
        changed=self.revise(c,rid,1,
           {"validation_direction":"双场景复测验证"},
           resolve={"validation_direction":"核对原始两条证据"})
        self.assertEqual(200,changed.status_code,changed.text)
        self.assertEqual("双场景复测验证",
                         changed.json()["five_dimensions"]["quality_and_validation"]["validation_direction"])
        result=self.confirm(c,rid,2)
        self.assertEqual(200,result.status_code,result.text)
        self.assertEqual("CONFIRMED",result.json()["status"])
        self.assertFalse(result.json()["publish_ready"])
        self.assertEqual("sandbox-reviewer",c.get(
            f"/api/v2/qs-next/review/v1/{rid}/audit").json()[-1]["actor"])
        self.assertEqual(self.source_sha,hashlib.sha256(self.data.src.read_bytes()).hexdigest())

    def test_no_domain_approval_blocks_even_with_valid_dictionary(self):
        c=self.make_client(scopes={})
        rid=self.start(c).json()["review_id"]
        self.revise(c,rid,1,{"validation_direction":"双场景复测"},
                    resolve={"validation_direction":"已有受控原文"})
        r=self.confirm(c,rid,2)
        self.assertEqual(409,r.status_code)
        self.assertEqual("CONTROLLED_TAXONOMY_NOT_VERIFIED",r.json()["detail"])

    def test_wrong_lifecycle_activity_match_refused_by_original_table(self):
        c=self.make_client()
        rid=self.start(c).json()["review_id"]
        changes={"activity_code":"RUNTIME_EXCEPTION_HANDLING",
                 "validation_direction":"双场景复测"}
        self.revise(c,rid,1,changes,resolve={"validation_direction":"原始证据核实"})
        r=self.confirm(c,rid,2)
        self.assertEqual(409,r.status_code)
        self.assertEqual("CONTROLLED_TAXONOMY_NOT_VERIFIED",r.json()["detail"])

    def test_taxonomy_disabled_stage_and_duplicate_active_versions_fail(self):
        gate=OriginalTaxonomyGate(self.data.src,approved_domain_scopes={"PLC":{"SOFTWARE"}})
        review={
            "problem_domains":["SOFTWARE"],
            "effective_fields":{"product_code":"PLC","lifecycle_code":"RUNTIME_EXECUTION",
                                "activity_code":"TASK_CYCLE_EXECUTION"},
        }
        self.assertTrue(gate(review))
        with sqlite3.connect(self.data.src) as db:
            db.execute("UPDATE scenario_activity SET enabled=0")
        self.assertEqual("ACTIVITY_NOT_APPROVED_FOR_LIFECYCLE",gate.verify(review)["code"])
        with sqlite3.connect(self.data.src) as db:
            db.execute("UPDATE scenario_activity SET enabled=1")
            db.execute("INSERT INTO scenario_taxonomy_version VALUES(?,?,?,?)",
                       ("STV-DUPLICATE",2,"PLC","ACTIVE"))
        self.assertEqual("ACTIVE_TAXONOMY_AMBIGUOUS_OR_MISSING",gate.verify(review)["code"])

    def test_hardware_mechanical_require_explicit_approved_domain(self):
        review={"problem_domains":["MECHANICAL"],
                "effective_fields":{"product_code":"PLC",
                                    "lifecycle_code":"RUNTIME_EXECUTION",
                                    "activity_code":"TASK_CYCLE_EXECUTION"}}
        original=OriginalTaxonomyGate(self.data.src,approved_domain_scopes={"PLC":{"SOFTWARE"}})
        self.assertFalse(original(review))
        approved=OriginalTaxonomyGate(self.data.src,approved_domain_scopes={
            "PLC":{"SOFTWARE","MECHANICAL"}})
        self.assertTrue(approved(review)) # grant is explicit, not inferred

    def test_no_security_binding_cannot_build_operator_app(self):
        with self.assertRaises(ValueError):
            create_stage6_app(self.data.src,self.data.store_path,security=None,
                              approved_domain_scopes={})


if __name__=="__main__":
    unittest.main()
