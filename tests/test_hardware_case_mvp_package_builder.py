from scripts import build_hardware_case_mvp_package as package_builder


def test_mvp_package_allowlist_contains_runtime_parser_dependency():
    assert "parser" in package_builder.INCLUDE_DIRS
    assert (package_builder.ROOT / "parser" / "pdf_extractor.py").is_file()


def test_r2_agent_package_contains_nonsensitive_agent_definitions_and_prompts():
    assert "config/runtime/agents" in package_builder.INCLUDE_DIRS
    assert "prompts/runtime/hardware_retrieval" in package_builder.INCLUDE_DIRS
    root = package_builder.ROOT
    assert (root / "config/runtime/agents/hardware_retrieval.query_understand.yaml").is_file()
    assert (root / "prompts/runtime/hardware_retrieval/query_understand_v1.md").is_file()

def test_r2_engineering_agent_package_closure():
    root = package_builder.ROOT
    assert (root / "config/runtime/agents/hardware_retrieval.engineering_consume.yaml").is_file()
    assert (root / "prompts/runtime/hardware_retrieval/engineering_consume_v1.md").is_file()
