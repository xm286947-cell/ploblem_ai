"""Hermetic contract + HTTP tests for opt-in PATCH57 material read gateway."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.qs_next_gateway import create_router


SCHEMA = """
CREATE TABLE data_group(group_id TEXT PRIMARY KEY,group_code TEXT,material_type TEXT);
CREATE TABLE source_material(
    material_id TEXT PRIMARY KEY,group_id TEXT,material_type TEXT,
    business_key TEXT,canonical_itr TEXT,version_no INTEGER,
    source_hash TEXT,raw_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE issue_material_link(
    link_id TEXT PRIMARY KEY,knowledge_id TEXT,material_id TEXT,link_status TEXT);
"""
GROUPS = (("DG-CS", "ITR-CS", "ITR_CS"),
          ("DG-MT", "ESCAPE", "ESCAPE_ANALYSIS"),
          ("DG-AS", "SW-OPS", "SOFTWARE_OPERATION"))


class TestLegacyMaterialReadGateway(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "source.sqlite3"
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO data_group VALUES(?,?,?)", GROUPS)
        self.conn = conn
        self.addCleanup(conn.close)
        self.insert("CS-SW", "ITR_CS", "ITR-SW", {"问题信息_问题领域": "软件"})
        self.insert("MT-SW", "ESCAPE_ANALYSIS", "ITR-SW", {"问题领域": "软件"})
        self.insert("AS-SW", "SOFTWARE_OPERATION", "ITR-SW", {})
        self.insert("CS-HW", "ITR_CS", "ITR-HW", {"问题信息_问题领域": "硬件"})
        self.insert("CS-MECH", "ITR_CS", "ITR-MECH", {"问题信息_问题领域": "机械"})
        self.insert("CS-MIX", "ITR_CS", "ITR-MIX",
                    {"问题信息_问题领域": "软件/硬件/机械"})
        self.insert("CS-UNKNOWN", "ITR_CS", "ITR-UNKNOWN", {"问题信息_故障描述": "模拟缺字段"})
        self.insert("AS-ONLY", "SOFTWARE_OPERATION", "ITR-ONLY", {})
        self.insert("MT-WRONG", "ESCAPE_ANALYSIS", "ITR-WRONG", {"问题领域": "机械"})
        self.insert("AS-HW", "SOFTWARE_OPERATION", "ITR-HW", {})
        self.insert("MT-HW", "ESCAPE_ANALYSIS", "ITR-HW", {"问题领域": "软件"})
        self.insert("CS-OLD", "ITR_CS", "ITR-REV", {"问题领域": "软件"}, business_key="REV-1", version=1)
        self.insert("CS-NEW", "ITR_CS", "ITR-REV", {"问题领域": "软件"}, business_key="REV-1", version=2)
        for mid, ref in (("CS-SW","K-SW"),("MT-SW","K-SW"),("AS-SW","K-SW"),
                         ("CS-HW","K-HW"),("AS-HW","K-HW"),("MT-HW","K-HW")):
            conn.execute("INSERT INTO issue_material_link VALUES(?,?,?,?)",
                         ("LNK-"+mid, ref, mid, "LINKED"))
        conn.commit()
        app = FastAPI()
        app.include_router(create_router(self.db))
        self.client = TestClient(app)
        self.base = "/api/v2/qs-next/entry/v1"

    def insert(self, mid, typ, itr, raw, *, business_key=None, version=1):
        group={"ITR_CS":"DG-CS", "ESCAPE_ANALYSIS":"DG-MT",
               "SOFTWARE_OPERATION":"DG-AS"}[typ]
        blob=json.dumps(raw,ensure_ascii=False)
        sha=hashlib.sha256(blob.encode()).hexdigest()
        self.conn.execute("""INSERT INTO source_material(
            material_id,group_id,material_type,business_key,canonical_itr,
            version_no,source_hash,raw_json) VALUES(?,?,?,?,?,?,?,?)""",
            (mid,group,typ,business_key or mid,itr,version,sha,blob))

    def get(self,wb,mid):
        return self.client.get(f"{self.base}/{wb}/{mid}")

    def test_three_domain_cs_entry(self):
        for mid,expected in (
            ("CS-SW",["SOFTWARE"]),("CS-HW",["HARDWARE"]),
            ("CS-MECH",["MECHANICAL"]),
            ("CS-MIX",["SOFTWARE","HARDWARE","MECHANICAL"]),
        ):
            with self.subTest(mid=mid):
                r=self.get("cs",mid)
                self.assertEqual(200,r.status_code)
                self.assertEqual(expected,r.json()["domains"])
                self.assertEqual("READY_FOR_SOURCE_READ",r.json()["status"])

    def test_unknown_cs_domain_requires_review(self):
        r=self.get("cs","CS-UNKNOWN")
        self.assertEqual(200,r.status_code)
        self.assertEqual("DOMAIN_REVIEW_REQUIRED",r.json()["status"])
        self.assertFalse(r.json()["can_extract_facts"])

    def test_missed_test_entry_stays_software(self):
        r=self.get("missed-test","MT-SW")
        self.assertEqual(200,r.status_code)
        self.assertEqual(["SOFTWARE"],r.json()["domains"])
        self.assertEqual("FULL",r.json()["source_coverage"])

    def test_assessment_entry_with_both_formal_sources(self):
        r=self.get("software-assessment","AS-SW")
        self.assertEqual(200,r.status_code)
        self.assertEqual("READY_FOR_SOURCE_READ",r.json()["status"])
        self.assertEqual("FULL",r.json()["source_coverage"])
        self.assertEqual({"THOROUGH_SOLUTION_ORDER","MISSED_TEST_ANALYSIS"},
                         {x["formal_source_type"] for x in r.json()["formal_source_reads"]})
        self.assertFalse(r.json()["candidate_created"])
        self.assertFalse(r.json()["published"])

    def test_assessment_with_no_verified_source_is_pending(self):
        r=self.get("software-assessment","AS-ONLY")
        self.assertEqual(200,r.status_code)
        self.assertEqual("FORMAL_SOURCE_REQUIRED",r.json()["status"])
        self.assertEqual("NONE",r.json()["source_coverage"])

    def test_assessment_can_use_software_missed_test_but_not_hardware_cs(self):
        r=self.get("software-assessment","AS-HW")
        self.assertEqual(200,r.status_code)
        self.assertEqual("READY_FOR_SOURCE_READ",r.json()["status"])
        self.assertEqual("PARTIAL",r.json()["source_coverage"])
        self.assertEqual(["MISSED_TEST_ANALYSIS"],
                         [s["formal_source_type"] for s in r.json()["formal_source_reads"]])
        self.assertEqual(["SOFTWARE"],r.json()["domains"])

    def test_software_missed_test_cannot_cover_hardware_only_cs(self):
        r=self.get("cs","CS-HW")
        self.assertEqual(200,r.status_code)
        self.assertEqual("PARTIAL",r.json()["source_coverage"])
        self.assertEqual(["THOROUGH_SOLUTION_ORDER"],
                         [s["formal_source_type"] for s in r.json()["formal_source_reads"]])
        self.assertEqual("HARDWARE",r.json()["domains"][0])

    def test_missed_test_rejects_hardware_mechanical(self):
        r=self.get("missed-test","MT-WRONG")
        self.assertEqual(409,r.status_code)
        self.assertEqual("DOMAIN_NOT_ALLOWED_FOR_WORKBENCH",r.json()["detail"])

    def test_stale_source_revision_rejected(self):
        r=self.get("cs","CS-OLD")
        self.assertEqual(409,r.status_code)
        self.assertEqual("STALE_SOURCE_REVISION",r.json()["detail"])
        self.assertEqual(200,self.get("cs","CS-NEW").status_code)

    def test_entry_type_and_missing_record_rejected(self):
        r=self.get("software-assessment","CS-SW")
        self.assertEqual(400,r.status_code)
        self.assertEqual("ENTRY_MATERIAL_TYPE_MISMATCH",r.json()["detail"])
        self.assertEqual(404,self.get("cs","NOT-FOUND").status_code)
        self.assertEqual(400,self.get("itr","CS-SW").status_code)

    def test_original_sqlite_content_not_mutated_by_queries(self):
        self.conn.commit()
        self.conn.close()
        before=hashlib.sha256(self.db.read_bytes()).digest()
        for _ in range(5):
            self.assertEqual(200,self.get("software-assessment","AS-SW").status_code)
            self.assertEqual(200,self.get("cs","CS-HW").status_code)
        self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).digest())


if __name__ == "__main__":
    unittest.main()
