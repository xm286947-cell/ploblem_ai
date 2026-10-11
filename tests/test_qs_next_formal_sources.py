"""Fast, hermetic checks for the additive QS formal-source boundary."""
from __future__ import annotations

import copy
import unittest

from quality_knowledge.qs_next_formal_sources import (
    FormalSourceError, normalize_formal_sources,
)


def source(kind="THOROUGH_SOLUTION_ORDER", record_id="CS-001", problem_ref="ITR-001"):
    return {
        "source_type": kind,
        "source_record_id": record_id,
        "problem_ref": problem_ref,
        "facts": {
            "failure_mode": "设备异常重启",
            "operating_condition": "持续运行",
        },
        "evidence": [{
            "evidence_id": f"E-{record_id}",
            "locator": "report://page/3/section/4",
            "excerpt": "记录中的原文事实",
            "supports": ["failure_mode"],
            "evidence_kind": "FACT",
        }],
    }


class TestFormalSourceAdapter(unittest.TestCase):
    def assert_error(self, code, records):
        with self.assertRaises(FormalSourceError) as captured:
            normalize_formal_sources(records)
        self.assertEqual(code, captured.exception.code)

    def test_cs_source_only_is_partial_and_evidence_preserved(self):
        record = source()
        original = copy.deepcopy(record)
        got = normalize_formal_sources([record])
        self.assertEqual("PARTIAL", got["source_coverage"])
        self.assertEqual(["THOROUGH_SOLUTION_ORDER:CS-001"], got["source_refs"])
        self.assertEqual("ITR-001", got["problem_ref_context"])
        self.assertEqual("FACT", got["sources"][0]["evidence"][0]["evidence_kind"])
        self.assertEqual(
            [{"source_ref": "THOROUGH_SOLUTION_ORDER:CS-001", "field": "operating_condition"}],
            got["missing_evidence"],
        )
        self.assertEqual("SOURCE_CONTEXT_ONLY", got["status"])
        self.assertFalse(got["publish_ready"])
        self.assertEqual(record, original)

    def test_missed_test_only_is_valid_partial(self):
        got = normalize_formal_sources(
            [source("MISSED_TEST_ANALYSIS", "MT-001", "")]
        )
        self.assertEqual("PARTIAL", got["source_coverage"])
        self.assertEqual("", got["problem_ref_context"])
        self.assertEqual(["MISSED_TEST_ANALYSIS"], got["production_source_types"])

    def test_linked_cs_and_missed_test_is_both_types(self):
        records = [
            source(),
            source("MISSED_TEST_ANALYSIS", "MT-001", "ITR-001"),
        ]
        got = normalize_formal_sources(records)
        self.assertEqual("FULL", got["source_coverage"])
        self.assertEqual(2, len(got["sources"]))
        self.assertEqual(2, len(got["missing_evidence"]))
        self.assertEqual(2, len(set(got["source_refs"])))

    def test_itr_cannot_be_formal_source(self):
        self.assert_error("UNSUPPORTED_FORMAL_SOURCE_TYPE", [{
            "source_type": "ITR",
            "source_record_id": "ITR-001",
            "facts": {},
            "evidence": [],
        }])

    def test_implicit_linking_is_forbidden(self):
        self.assert_error("SOURCE_RELATION_UNVERIFIED", [
            source(problem_ref="ITR-001"),
            source("MISSED_TEST_ANALYSIS", "MT-001", "ITR-002"),
        ])
        self.assert_error("SOURCE_RELATION_UNVERIFIED", [
            source(problem_ref=""),
            source("MISSED_TEST_ANALYSIS", "MT-001", ""),
        ])

    def test_duplicate_and_missing_source_identity(self):
        self.assert_error("DUPLICATE_FORMAL_SOURCE", [source(), source()])
        bad = source()
        bad["source_record_id"] = ""
        self.assert_error("SOURCE_RECORD_ID_REQUIRED", [bad])

    def test_empty_bundle_is_rejected(self):
        self.assert_error("FORMAL_SOURCE_REQUIRED", [])
        self.assert_error("INVALID_SOURCE_LIST", {"not": "a list"})

    def test_source_evidence_cannot_cross_link(self):
        bad = source()
        bad["evidence"][0]["source_ref"] = "MISSED_TEST_ANALYSIS:MT-001"
        self.assert_error("EVIDENCE_SOURCE_REF_MISMATCH", [bad])

    def test_evidence_requires_locator_or_excerpt_and_known_supports(self):
        bad = source()
        bad["evidence"][0].update({"locator": "", "excerpt": ""})
        self.assert_error("EVIDENCE_CONTENT_REQUIRED", [bad])
        bad = source()
        bad["evidence"][0]["supports"] = ["invented_field"]
        self.assert_error("EVIDENCE_SUPPORTS_UNKNOWN_FACT", [bad])

    def test_inference_cannot_satisfy_factual_evidence(self):
        record = source()
        record["evidence"][0]["evidence_kind"] = "INFERRED"
        got = normalize_formal_sources([record])
        self.assertEqual(
            {"failure_mode", "operating_condition"},
            {item["field"] for item in got["missing_evidence"]},
        )
        self.assertEqual("INFERRED", got["sources"][0]["evidence"][0]["evidence_kind"])

    def test_confirmed_fact_requires_explicit_provenance_kind(self):
        record = source()
        record["evidence"][0]["evidence_kind"] = "HUMAN_CONFIRMED"
        got = normalize_formal_sources([record])
        self.assertEqual(["operating_condition"], got["sources"][0]["fields_without_evidence"])

    def test_evidence_confidence_is_bounded(self):
        record = source()
        record["evidence"][0]["confidence"] = 1.2
        self.assert_error("INVALID_EVIDENCE_CONFIDENCE", [record])
        record["evidence"][0]["confidence"] = 0.9
        self.assertEqual(0.9, normalize_formal_sources([record])["sources"][0]["evidence"][0]["confidence"])

    def test_no_evidence_or_fact_is_not_silently_promoted(self):
        record = source()
        record["evidence"] = []
        got = normalize_formal_sources([record])
        self.assertFalse(got["publish_ready"])
        self.assertEqual(2, len(got["missing_evidence"]))
        record["facts"] = {}
        self.assertEqual([], normalize_formal_sources([record])["missing_evidence"])


if __name__ == "__main__":
    unittest.main()
