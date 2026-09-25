from pathlib import Path

from storage_life import parameter_baseline


def test_four_device_families_have_three_frozen_groups_and_must_slots():
    for dtype in ("eMMC", "SSD", "NAND Flash", "NOR Flash"):
        fields = parameter_baseline.product_fields(dtype, [])
        groups = {x["group"] for x in fields}
        assert parameter_baseline.KEY_SPEC in groups
        assert parameter_baseline.KEY_DIAGNOSTIC in groups
        assert any(x["requirement_level"] == "MUST" for x in fields if x["group"] == parameter_baseline.KEY_SPEC)
        assert any(x["requirement_level"] == "MUST" for x in fields if x["group"] == parameter_baseline.KEY_DIAGNOSTIC)


def test_frozen_baseline_keeps_required_slots_when_extraction_returns_nothing():
    fields = parameter_baseline.product_fields("SSD", [])
    ids = {x["canonical_name"] for x in fields}
    assert {"capacity", "tbw", "percentage_used", "available_spare", "critical_warning", "data_units_written", "media_errors", "smart_health"} <= ids


def test_comprehensive_group_appends_non_key_extracted_fields():
    fields = parameter_baseline.product_fields("NOR Flash", [{"canonical_name": "clock_frequency", "parameter_name": "时钟频率（Clock Frequency）"}])
    item = next(x for x in fields if x["canonical_name"] == "clock_frequency")
    assert item["group"] == parameter_baseline.COMPREHENSIVE


def test_p08_exposes_identity_confirmation_parameter_coverage_and_chinese_actions():
    html = (Path(__file__).resolve().parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    for text in ("基础信息自动识别", "确认基础信息", "参数识别与覆盖度", "关键说明参数", "关键诊断参数", "全面参数", "修改并确认", "已找到", "未找到", "未检查", "有歧义"):
        assert text in html
    assert "id=\"pdfFile\"" in html
    assert "/api/documents/identify" in html
    assert "IDENTITY_CONFIRMED" in html
