from scripts import build_hardware_case_mvp_package as package_builder


def test_mvp_package_allowlist_contains_runtime_parser_dependency():
    assert "parser" in package_builder.INCLUDE_DIRS
    assert (package_builder.ROOT / "parser" / "pdf_extractor.py").is_file()
