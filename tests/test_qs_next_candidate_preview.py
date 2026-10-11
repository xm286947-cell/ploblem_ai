"""Stage4: real SQLite-shaped material field extraction; no Provider or writes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from quality_knowledge.qs_next_candidate_preview import create_candidate_preview_router
from quality_knowledge.qs_next_gateway import create_router as create_gateway_router


SCHEMA = """
CREATE TABLE data_group(group_id TEXT PRIMARY KEY,group_code TEXT,material_type TEXT);
CREATE TABLE source_material(
  material_id TEXT PRIMARY KEY,group_id TEXT,material_type TEXT,business_key TEXT,
  canonical_itr TEXT,version_no INTEGER,source_hash TEXT,raw_json TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE issue_material_link(
  link_id TEXT PRIMARY KEY,knowledge_id TEXT,material_id TEXT,link_status TEXT);
"""


class TestStage4Preview(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "test_materials.db"
        self.con = sqlite3.connect(self.db)
        self.con.executescript(SCHEMA)
        self.con.executemany("INSERT INTO data_group VALUES(?,?,?)", [
            ("DG-CS", "ITR-CS", "ITR_CS"),
            ("DG-MT", "ESCAPE", "ESCAPE_ANALYSIS"),
            ("DG-AS", "SW-OPS", "SOFTWARE_OPERATION"),
        ])
        self.cs_sw = {
            "问题信息_问题领域": "软件",
            "问题信息_产品型号": "PLC-X",
            "问题信息_客户名称": "测试客户",
            "问题信息_故障现象描述": "运行中异常复位",
            "技术根因分析与纠正_产品层级软件失效模式": "周期任务异常",
            "技术根因分析与纠正_功能层级软件失效机理": "软件定时调度错误",
            "运行工况": "持续运行",
            "触发条件": "累计运行超过设定时长",
            "业务影响": "生产中断",
            "客户质量体验要求": "稳定运行且安全恢复",
            "测试验证方向": "长时间回归测试",
        }
        self.insert("CS-SW", "ITR_CS", "ITR-SW", self.cs_sw)
        self.insert("MT-SW", "ESCAPE_ANALYSIS", "ITR-SW", {
            "问题领域": "软件",
            "测试漏测原因": "未覆盖持续运行时序",
            "测试验证方向": "补充长稳测试",
        })
        self.insert("AS-SW", "SOFTWARE_OPERATION", "ITR-SW", {
            "问题领域": "软件", "客户名称": "禁止作为正式场景证据",
            "业务影响": "来源于 KPI，不可映射",
        })
        self.insert("CS-HW", "ITR_CS", "ITR-HW", {
            "问题信息_问题领域": "硬件",
            "技术根因分析与纠正_器件失效模式": "器件开路",
            "技术根因分析与纠正_器件失效机理": "已记录焊点接触异常",
            "技术根因分析与纠正_产品层级软件失效模式": "不属于此硬件场景",
        })
        self.insert("CS-MECH", "ITR_CS", "ITR-MECH", {
            "问题信息_问题领域": "机械",
            "技术根因分析与纠正_产品层级机械失效模式": "机构卡滞",
            "技术根因分析与纠正_部件层级机械机理/根因": "机械摩擦异常",
        })
        self.insert("CS-MIX", "ITR_CS", "ITR-MIX", {
            "问题信息_问题领域": "软件/硬件/机械",
            "技术根因分析与纠正_产品层级软件失效模式": "通讯超时",
            "技术根因分析与纠正_器件失效模式": "传感器开路",
            "技术根因分析与纠正_产品层级机械失效模式": "导轨卡滞",
        })
        self.insert("CS-UNK", "ITR_CS", "ITR-UNK", {"故障现象描述": "未分类"})
        self.insert("AS-ONLY", "SOFTWARE_OPERATION", "ITR-ONLY", {"业务影响": "KPI"})
        self.insert("CS-OLD", "ITR_CS", "ITR-V", {"问题领域": "软件"},
                    business_key="same", version=1)
        self.insert("CS-NEW", "ITR_CS", "ITR-V", {"问题领域": "软件"},
                    business_key="same", version=2)
        for mid, group in (
            ("CS-SW", "K-SW"), ("MT-SW", "K-SW"), ("AS-SW", "K-SW"),
            ("CS-HW", "K-HW"),
        ):
            self.con.execute("INSERT INTO issue_material_link VALUES(?,?,?,?)",
                             (f"LINK-{mid}", group, mid, "LINKED"))
        self.con.commit()
        app = FastAPI()
        app.include_router(create_gateway_router(self.db))
        app.include_router(create_candidate_preview_router(self.db))
        self.client = TestClient(app)

    def insert(self, material_id, material_type, itr, raw, *, business_key=None, version=1):
        group = {"ITR_CS":"DG-CS", "ESCAPE_ANALYSIS":"DG-MT",
                 "SOFTWARE_OPERATION":"DG-AS"}[material_type]
        blob = json.dumps(raw, ensure_ascii=False)
        self.con.execute("""
          INSERT INTO source_material
          (material_id,group_id,material_type,business_key,canonical_itr,version_no,source_hash,raw_json)
          VALUES (?,?,?,?,?,?,?,?)""",
          (material_id, group, material_type, business_key or material_id, itr,
           version, hashlib.sha256(blob.encode()).hexdigest(), blob))

    def request(self, workbench, material):
        return self.client.get(f"/api/v2/qs-next/candidate/v1/{workbench}/{material}")

    def test_cs_software_candidate_with_five_dimensional_fields(self):
        r = self.request("cs", "CS-SW")
        self.assertEqual(200, r.status_code)
        result = r.json()
        self.assertEqual("CANDIDATE_PREVIEW", result["status"])
        c = result["candidate"]
        self.assertEqual(5, len(c["dimensions"]))
        self.assertEqual(["SOFTWARE"], c["problem_domains"])
        self.assertEqual("PLC-X", c["dimensions"]["usage_context"]["product_model"])
        self.assertEqual("周期任务异常", c["dimensions"]["failure_behavior"]["software_failure_mode"])
        self.assertEqual("持续运行", c["dimensions"]["trigger_and_conditions"]["operating_condition"])
        self.assertEqual("生产中断", c["dimensions"]["customer_impact"]["business_impact"])
        self.assertEqual("长时间回归测试", c["dimensions"]["quality_and_validation"]["validation_direction"])
        self.assertEqual("PENDING_HUMAN_REVIEW", c["status"])
        self.assertFalse(c["publish_ready"])
        self.assertFalse(result["persisted"])

    def test_source_evidence_contains_actual_raw_field_and_revision(self):
        evidence = self.request("cs", "CS-SW").json()["candidate"]["field_evidence"]["software_failure_mode"]
        self.assertEqual("CS-SW", evidence["material_id"])
        self.assertEqual(1, evidence["source_revision"])
        self.assertEqual(64, len(evidence["source_hash"]))
        self.assertEqual("技术根因分析与纠正_产品层级软件失效模式", evidence["raw_field"])
        self.assertIn("raw_json", evidence["locator"])
        self.assertEqual("FACT", evidence["evidence_kind"])

    def test_assessment_entry_uses_only_official_sources_not_kpi_fields(self):
        r = self.request("software-assessment", "AS-SW")
        self.assertEqual(200, r.status_code)
        c = r.json()["candidate"]
        self.assertEqual("FULL", c["source_coverage"])
        self.assertEqual("测试客户", c["dimensions"]["usage_context"]["customer_name"])
        self.assertNotIn("禁止作为正式场景证据",
                         json.dumps(c, ensure_ascii=False))
        self.assertNotIn("来源于 KPI", json.dumps(c, ensure_ascii=False))
        self.assertIn("escape_reason", c["dimensions"]["quality_and_validation"])

    def test_missed_test_only_is_software_and_can_be_candidate(self):
        r = self.request("missed-test", "MT-SW")
        self.assertEqual(200, r.status_code)
        c = r.json()["candidate"]
        self.assertEqual(["SOFTWARE"], c["problem_domains"])
        self.assertEqual("未覆盖持续运行时序", c["dimensions"]["quality_and_validation"]["escape_reason"])

    def test_hardware_scenario_does_not_import_software_mode(self):
        c = self.request("cs", "CS-HW").json()["candidate"]
        failures = c["dimensions"]["failure_behavior"]
        self.assertEqual("器件开路", failures["hardware_failure_mode"])
        self.assertNotIn("software_failure_mode", failures)

    def test_mechanical_scenario_field_specificity(self):
        c = self.request("cs", "CS-MECH").json()["candidate"]
        failures = c["dimensions"]["failure_behavior"]
        self.assertEqual("机构卡滞", failures["mechanical_failure_mode"])
        self.assertNotIn("hardware_failure_mode", failures)

    def test_mixed_domain_preserves_three_independent_modes(self):
        c = self.request("cs", "CS-MIX").json()["candidate"]
        self.assertEqual(["SOFTWARE", "HARDWARE", "MECHANICAL"], c["problem_domains"])
        failures = c["dimensions"]["failure_behavior"]
        self.assertEqual(3, sum(k in failures for k in (
            "software_failure_mode", "hardware_failure_mode", "mechanical_failure_mode")))

    def test_conflicting_source_fields_require_human_review(self):
        c = self.request("software-assessment", "AS-SW").json()["candidate"]
        self.assertIn("SOURCE_FIELD_CONFLICT", self.request("software-assessment", "AS-SW").json()["blockers"])
        self.assertTrue(any(x["field"]=="validation_direction" for x in c["conflicts"]))
        self.assertEqual("长时间回归测试", c["legacy_compatible_fields"]["validation_direction"])
        self.assertEqual(2, len(c["all_field_evidence"]["validation_direction"]))

    def test_unknown_domain_and_no_formal_source_blocked(self):
        for wb, mid, blocker in [
            ("cs", "CS-UNK", "DOMAIN_REVIEW_REQUIRED"),
            ("software-assessment", "AS-ONLY", "FORMAL_SOURCE_REQUIRED"),
        ]:
            r = self.request(wb,mid)
            self.assertEqual(200,r.status_code)
            self.assertEqual("BLOCKED",r.json()["status"])
            self.assertIsNone(r.json()["candidate"])
            self.assertIn(blocker,r.json()["blockers"])

    def test_old_revision_and_unexpected_material_rejected(self):
        r = self.request("cs", "CS-OLD")
        self.assertEqual(409,r.status_code)
        self.assertEqual("STALE_SOURCE_REVISION",r.json()["detail"])
        self.assertEqual(400,self.request("software-assessment","CS-SW").status_code)
        self.assertEqual(404,self.request("cs","DOES-NOT-EXIST").status_code)

    def test_preview_id_stable_non_persisted_and_five_dimensions(self):
        one = self.request("cs", "CS-SW").json()["candidate"]
        two = self.request("cs", "CS-SW").json()["candidate"]
        self.assertEqual(one["preview_id"], two["preview_id"])
        self.assertTrue(one["preview_id"].startswith("QS-PREVIEW-"))
        self.assertEqual(5,len(one["dimensions"]))
        self.assertEqual("PENDING",one["review"]["status"])

    def test_missing_activity_and_validation_require_confirmation(self):
        c = self.request("cs", "CS-HW").json()["candidate"]
        self.assertIn("activity_code",c["missing_information"])
        self.assertIn("validation_direction",c["missing_information"])
        self.assertIn("LIFECYCLE_ACTIVITY_TAXONOMY_UNVERIFIED",self.request("cs","CS-HW").json()["blockers"])

    def test_no_sqlite_mutation_from_real_http_requests(self):
        self.con.commit()
        self.con.close()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        for wb, mid in (("cs","CS-SW"),("cs","CS-HW"),
                        ("missed-test","MT-SW"),("software-assessment","AS-SW")):
            r=self.request(wb,mid)
            self.assertEqual(200,r.status_code)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_formal_source_revision_identity_is_bound(self):
        c=self.request("cs","CS-SW").json()["candidate"]
        records=c["source_refs"]
        self.assertEqual({"ITR_CS", "ESCAPE_ANALYSIS"}, {x["material_type"] for x in records})
        self.assertTrue(all(x["source_hash"] and x["version_no"] >= 1 for x in records))


if __name__ == "__main__":
    unittest.main()
