"""No database or provider: three-entry Quality Scenario contract acceptance."""
from __future__ import annotations

import copy
import unittest

from quality_knowledge.qs_next_entry_routing import (
    EntryContractError,
    resolve_quality_scenario_entry as resolve,
)


SHA = "a" * 64


def entry(workbench="THOROUGH_SOLUTION", **changes):
    types = {
        "THOROUGH_SOLUTION": "ITR_CS",
        "MISSED_TEST": "ESCAPE_ANALYSIS",
        "SOFTWARE_ASSESSMENT": "SOFTWARE_OPERATION",
    }
    row = {
        "workbench": workbench,
        "material_type": types[workbench],
        "material_id": "MAT-ENTRY",
        "version_no": 2,
        "source_hash": SHA,
        "canonical_problem_ref": "ITR-2026-01",
    }
    if workbench == "THOROUGH_SOLUTION":
        row["domains"] = ["SOFTWARE"]
    row.update(changes)
    return row


def formal_source(material_type="ITR_CS", **changes):
    row = {
        "material_type": material_type,
        "material_id": "MAT-LINKED",
        "version_no": 3,
        "source_hash": "b" * 64,
        "canonical_problem_ref": "ITR-2026-01",
        "link_status": "LINKED",
        "relation_ref": "LNK-001",
        "domains": ["SOFTWARE"],
    }
    row.update(changes)
    return row


class TestThreeEntryContract(unittest.TestCase):
    def error(self, code, item, links=()):
        with self.assertRaises(EntryContractError) as exc:
            resolve(item, links)
        self.assertEqual(code, exc.exception.code)

    def test_thorough_solution_software_primary(self):
        got = resolve(entry())
        self.assertEqual(["SOFTWARE"], got["problem_domains"])
        self.assertEqual("READY_FOR_SOURCE_READ", got["status"])
        self.assertEqual("THOROUGH_SOLUTION_ORDER", got["formal_source_reads"][0]["formal_source_type"])
        self.assertEqual("PARTIAL", got["source_coverage"])
        self.assertFalse(got["candidate_created"])
        self.assertFalse(got["published"])

    def test_thorough_solution_hardware(self):
        got = resolve(entry(domains=["HARDWARE"]))
        self.assertEqual(["HARDWARE"], got["problem_domains"])
        self.assertTrue(got["can_extract_facts"])

    def test_thorough_solution_mechanical(self):
        got = resolve(entry(domains=["MECHANICAL"]))
        self.assertEqual(["MECHANICAL"], got["problem_domains"])
        self.assertTrue(got["can_extract_facts"])

    def test_thorough_solution_cross_domain(self):
        got = resolve(entry(domains=["SOFTWARE", "HARDWARE", "MECHANICAL"]))
        self.assertEqual(["SOFTWARE", "HARDWARE", "MECHANICAL"], got["problem_domains"])
        self.assertEqual("EXPLICIT", got["domain_status"])

    def test_thorough_solution_unknown_domain_needs_review_not_guessed(self):
        got = resolve(entry(domains=[]))
        self.assertEqual([], got["problem_domains"])
        self.assertEqual("DOMAIN_REVIEW_REQUIRED", got["status"])
        self.assertFalse(got["can_extract_facts"])

    def test_missed_test_defaults_to_software_controlled_scope(self):
        got = resolve(entry("MISSED_TEST"))
        self.assertEqual(["SOFTWARE"], got["problem_domains"])
        self.assertEqual("WORKBENCH_POLICY", got["domain_status"])
        self.assertEqual("MISSED_TEST_ANALYSIS", got["formal_source_reads"][0]["formal_source_type"])

    def test_assessment_entry_without_formal_evidence_is_pending(self):
        got = resolve(entry("SOFTWARE_ASSESSMENT"))
        self.assertEqual(["SOFTWARE"], got["problem_domains"])
        self.assertEqual("FORMAL_SOURCE_REQUIRED", got["status"])
        self.assertEqual("NONE", got["source_coverage"])
        self.assertEqual([], got["formal_source_reads"])

    def test_assessment_with_linked_cs_becomes_source_read_plan(self):
        got = resolve(entry("SOFTWARE_ASSESSMENT"), [formal_source()])
        self.assertEqual("READY_FOR_SOURCE_READ", got["status"])
        self.assertEqual(["SOFTWARE"], got["problem_domains"])
        self.assertEqual("ITR_CS", got["formal_source_reads"][0]["material_type"])
        self.assertEqual("PARTIAL", got["source_coverage"])

    def test_assessment_with_linked_missed_test(self):
        got = resolve(entry("SOFTWARE_ASSESSMENT"),
                      [formal_source("ESCAPE_ANALYSIS")])
        self.assertEqual("MISSED_TEST_ANALYSIS", got["formal_source_reads"][0]["formal_source_type"])

    def test_assessment_with_two_official_sources_full_coverage_not_publish(self):
        links = [
            formal_source("ITR_CS", material_id="MAT-CS"),
            formal_source("ESCAPE_ANALYSIS", material_id="MAT-MISSED"),
        ]
        got = resolve(entry("SOFTWARE_ASSESSMENT"), links)
        self.assertEqual("FULL", got["source_coverage"])
        self.assertEqual("READY_FOR_SOURCE_READ", got["status"])
        self.assertFalse(got["published"])
        self.assertFalse(got["candidate_created"])

    def test_missed_test_with_linked_cs_keeps_original_source_identity(self):
        got = resolve(entry("MISSED_TEST"), [formal_source()])
        self.assertEqual("FULL", got["source_coverage"])
        self.assertEqual("ESCAPE_ANALYSIS", got["formal_source_reads"][0]["material_type"])
        self.assertEqual(2, len(got["formal_source_reads"]))

    def test_missed_and_assessment_reject_hardware_or_mechanical(self):
        for wb in ("MISSED_TEST", "SOFTWARE_ASSESSMENT"):
            for domain in ("HARDWARE", "MECHANICAL"):
                with self.subTest(wb=wb, domain=domain):
                    self.error("DOMAIN_NOT_ALLOWED_FOR_WORKBENCH", entry(wb, domains=[domain]))

    def test_software_entry_cannot_expand_from_hardware_only_cs(self):
        self.error("RELATED_SOURCE_DOMAIN_MISMATCH",
                   entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(domains=["HARDWARE"])])

    def test_software_entry_can_read_multidomain_cs_without_promoting_entry(self):
        got = resolve(entry("SOFTWARE_ASSESSMENT"),
                      [formal_source(domains=["SOFTWARE", "HARDWARE"])])
        self.assertEqual(["SOFTWARE"], got["problem_domains"])
        self.assertEqual(["SOFTWARE", "HARDWARE"], got["formal_source_reads"][0]["domains"])

    def test_cross_problem_relation_fails_closed(self):
        self.error("SOURCE_CONTEXT_MISMATCH", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(canonical_problem_ref="ITR-2026-02")])
        self.error("SOURCE_RELATION_UNVERIFIED", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(canonical_problem_ref="")])

    def test_link_requires_verified_status_and_real_relation_ref(self):
        self.error("SOURCE_RELATION_UNVERIFIED", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(link_status="CONFLICT")])
        self.error("SOURCE_RELATION_UNVERIFIED", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(link_status="ITR_NOT_FOUND")])
        self.error("SOURCE_RELATION_REF_REQUIRED", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(relation_ref="")])
        got = resolve(entry("SOFTWARE_ASSESSMENT"),
                      [formal_source(link_status="MANUAL_LINKED")])
        self.assertEqual("MANUAL_LINKED", got["formal_source_reads"][0]["relation"])

    def test_itr_source_is_not_a_formal_production_source(self):
        self.error("RELATED_SOURCE_NOT_FORMAL", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source("ITR_SOURCE")])

    def test_material_type_must_match_selected_entry(self):
        self.error("ENTRY_MATERIAL_TYPE_MISMATCH",
                   entry("SOFTWARE_ASSESSMENT", material_type="ITR_CS"))

    def test_source_identity_and_version_are_bound(self):
        self.error("MATERIAL_ID_REQUIRED", entry(material_id=""))
        self.error("INVALID_SOURCE_REVISION", entry(version_no=0))
        self.error("INVALID_SOURCE_REVISION", entry(version_no=True))
        self.error("INVALID_SOURCE_HASH", entry(source_hash="not-a-hash"))

    def test_duplicate_link_reference_is_rejected(self):
        self.error("DUPLICATE_SOURCE_REF", entry("SOFTWARE_ASSESSMENT"),
                   [formal_source(), formal_source()])
        self.error("DUPLICATE_SOURCE_REF", entry(),
                   [formal_source(material_id="MAT-ENTRY")])

    def test_bad_domain_inputs_are_rejected(self):
        self.error("UNSUPPORTED_PROBLEM_DOMAIN", entry(domains=["ELECTRICAL"]))
        self.error("DUPLICATE_PROBLEM_DOMAIN", entry(domains=["SOFTWARE", "SOFTWARE"]))
        self.error("INVALID_DOMAIN_LIST", entry(domains="HARDWARE"))

    def test_source_inputs_unchanged_and_no_write(self):
        e = entry("SOFTWARE_ASSESSMENT")
        linked = [formal_source()]
        snapshot = copy.deepcopy((e, linked))
        got = resolve(e, linked)
        self.assertEqual((e, linked), snapshot)
        self.assertEqual("MAT-LINKED", got["formal_source_reads"][0]["material_id"])
        self.assertEqual(3, got["formal_source_reads"][0]["version_no"])
        self.assertEqual("b" * 64, got["formal_source_reads"][0]["source_hash"])
        self.assertFalse(got["candidate_created"])

    def test_malformed_workbench_and_related_input_fails(self):
        self.error("INVALID_ENTRY", None)
        self.error("UNSUPPORTED_ENTRY_WORKBENCH", {"workbench": "ITR"})
        self.error("INVALID_RELATED_SOURCE_LIST", entry(), "bad-list")


if __name__ == "__main__":
    unittest.main()
