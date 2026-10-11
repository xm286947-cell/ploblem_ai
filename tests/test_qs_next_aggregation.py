"""Aggregation suggestions are read-only and require separate confirmed problems."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.qs_next_aggregation import suggest_pairs,create_suggestion_router
from quality_knowledge.qs_next_review import ReviewStore
import test_qs_next_review as fixture


class TestStage5Aggregation(unittest.TestCase):
    def setUp(self):
        self.data=fixture.TestStage5Review(
            "test_review_creation_persists_only_to_new_database")
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        with sqlite3.connect(self.data.src) as db:
            original=json.loads(db.execute(
                "SELECT raw_json FROM source_material WHERE material_id='CS-1'"
            ).fetchone()[0])
            self.data.insert(db,"CS-2","ITR_CS","ITR-002",original)
            differing=dict(original)
            differing["activity_code"]="LONG_TERM_COMMUNICATION"
            self.data.insert(db,"CS-3","ITR_CS","ITR-003",differing)
            different_product=dict(original)
            different_product["产品编码"]="OTHER"
            self.data.insert(db,"CS-4","ITR_CS","ITR-004",different_product)
        self.store=ReviewStore(self.data.src,self.data.store_path)

    def confirm(self,wb,material,*,resolve_conflict=False):
        c=self.data.client(taxonomy=True)
        start=self.data.start(c,wb,material)
        self.assertEqual(200,start.status_code,start.text)
        rid=start.json()["review_id"]
        rev=start.json()["revision"]
        if resolve_conflict:
            patched=self.data.revise(c,rid,rev,
                {"validation_direction":"长时间执行验证"},
                conflicts={"validation_direction":"核验两份材料后确认该要求"})
            self.assertEqual(200,patched.status_code,patched.text)
            rev=patched.json()["revision"]
        result=self.data.decide(c,rid,rev)
        self.assertEqual(200,result.status_code,result.text)
        return rid

    def test_different_confirmed_problems_propose_a_single_pair(self):
        a=self.confirm("cs","CS-1",resolve_conflict=True)
        b=self.confirm("cs","CS-2")
        out=suggest_pairs(self.store)
        self.assertEqual(1,out["pair_count"])
        self.assertEqual("SUGGESTION_ONLY",out["status"])
        pair=out["suggested_pairs"][0]
        self.assertEqual({a,b},{x["review_id"] for x in pair["members"]})
        self.assertEqual({"ITR-001","ITR-002"},{x["problem_ref"] for x in pair["members"]})
        self.assertEqual("SOFTWARE",pair["problem_domain"])
        self.assertEqual("PLC",pair["shared_product_code"])
        self.assertFalse(pair["asset_created"])
        self.assertFalse(pair["auto_grouped"])
        self.assertTrue(all(x["origin_evidence"] for x in pair["members"]))

    def test_unconfirmed_or_different_product_or_activity_are_not_suggested(self):
        self.confirm("cs","CS-1",resolve_conflict=True)
        self.data.start(self.data.client(),"cs","CS-2")
        self.confirm("cs","CS-3")
        self.confirm("cs","CS-4")
        self.assertEqual(0,suggest_pairs(self.store)["pair_count"])

    def test_same_problem_in_two_workbenches_is_not_a_multi_problem_pair(self):
        self.confirm("cs","CS-1",resolve_conflict=True)
        self.confirm("software-assessment","AS-1",resolve_conflict=True)
        out=suggest_pairs(self.store)
        self.assertEqual(0,out["pair_count"])

    def test_stale_source_review_excluded(self):
        self.confirm("cs","CS-1",resolve_conflict=True)
        self.confirm("cs","CS-2")
        with sqlite3.connect(self.data.src) as db:
            db.execute("UPDATE source_material SET source_hash=? WHERE material_id='CS-2'",
                       ("f"*64,))
        result=suggest_pairs(self.store)
        self.assertEqual(0,result["pair_count"])
        self.assertEqual(1,result["skipped_stale_review_count"])

    def test_suggestion_http_requires_trusted_role_and_does_not_write_db(self):
        self.confirm("cs","CS-1",resolve_conflict=True)
        self.confirm("cs","CS-2")
        original_source=hashlib.sha256(self.data.src.read_bytes()).hexdigest()
        original_store=hashlib.sha256(self.data.store_path.read_bytes()).hexdigest()
        app=FastAPI()
        app.include_router(create_suggestion_router(self.store))
        blocked=TestClient(app).get("/api/v2/qs-next/aggregation/v1/suggestions")
        self.assertEqual(403,blocked.status_code)
        app2=FastAPI()
        app2.include_router(create_suggestion_router(
            self.store,trusted_actor=lambda request:"quality-reviewer",
            authorize=lambda actor,action,subject: (
                action=="AGGREGATION_READ" and subject=="QUALITY_SCENARIOS")))
        ok=TestClient(app2).get("/api/v2/qs-next/aggregation/v1/suggestions")
        self.assertEqual(200,ok.status_code,ok.text)
        self.assertEqual(1,ok.json()["pair_count"])
        self.assertEqual(original_source,hashlib.sha256(self.data.src.read_bytes()).hexdigest())
        self.assertEqual(original_store,hashlib.sha256(self.data.store_path.read_bytes()).hexdigest())


if __name__=="__main__":
    unittest.main()
