from scripts import build_hardware_case_mvp_package as package_builder


def test_mvp_package_allowlist_contains_runtime_parser_dependency():
    assert "parser" in package_builder.INCLUDE_DIRS
    assert (package_builder.ROOT / "parser" / "pdf_extractor.py").is_file()


def test_r2_agent_runtime_definition_and_prompt_are_shipped():
    for relative in (
        "config/runtime/agents/hardware_r2.query.yaml",
        "config/runtime/agents/hardware_r2.consume.yaml",
        "prompts/runtime/hardware_r2/query_v1.md",
        "prompts/runtime/hardware_r2/consume_v1.md",
    ):
        assert relative in package_builder.INCLUDE_FILES
        assert (package_builder.ROOT / relative).is_file()
