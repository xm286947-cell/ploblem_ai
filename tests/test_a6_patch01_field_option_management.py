from fastapi.testclient import TestClient
from quality_knowledge.human_analysis import HumanAnalysisRepository, HumanAnalysisService
from quality_knowledge.web.app import create_app

def test_field_edit_and_option_management(tmp_path):
    db = tmp_path / "k.db"
    svc = HumanAnalysisService(HumanAnalysisRepository(db))
    field = svc.create_field(
        field_key="risk",
        field_name="风险等级",
        field_type="SINGLE_SELECT",
        options=[{"value":"HIGH","label":"高"},{"value":"LOW","label":"低"}],
        required=False,
    )
    client = TestClient(create_app(db))
    r = client.get(f"/settings/human-analysis/fields/{field['field_id']}")
    assert r.status_code == 200
    assert "选项管理" in r.text
    assert "HIGH" in r.text

    r = client.post(
        f"/settings/human-analysis/fields/{field['field_id']}",
        data={
            "field_name":"风险判断",
            "description":"人工复盘风险",
            "display_order":"10",
            "required":"on",
            "enabled":"on",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    updated = svc.list_field_definitions()[0]
    assert updated["field_name"] == "风险判断"
    assert updated["required"] == 1
    assert updated["field_key"] == "risk"
    assert updated["field_type"] == "SINGLE_SELECT"

    r = client.post(
        f"/settings/human-analysis/fields/{field['field_id']}/options",
        data={"option_value":"MEDIUM","option_label":"中","display_order":"20"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    updated = svc.list_field_definitions()[0]
    assert any(x["option_value"]=="MEDIUM" and x["option_label"]=="中" for x in updated["options"])

    low = next(x for x in updated["options"] if x["option_value"]=="LOW")
    r = client.post(
        f"/settings/human-analysis/options/{low['option_id']}",
        data={"option_label":"较低","display_order":"30"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    latest = svc.list_field_definitions()[0]
    low2 = next(x for x in latest["options"] if x["option_value"]=="LOW")
    assert low2["option_label"] == "较低"
    assert low2["enabled"] == 0
    assert low2["option_value"] == "LOW"

def test_used_option_can_be_disabled_without_losing_history(tmp_path):
    db = tmp_path / "k.db"
    svc = HumanAnalysisService(HumanAnalysisRepository(db))
    field = svc.create_field(
        field_key="risk",
        field_name="风险",
        field_type="SINGLE_SELECT",
        options=[{"value":"HIGH","label":"高"},{"value":"LOW","label":"低"}],
    )
    svc.save_analysis("K1","V1",{field["field_id"]:"LOW"})
    low = next(x for x in svc.list_field_definitions()[0]["options"] if x["option_value"]=="LOW")
    assert svc.option_is_used(field["field_id"], "LOW") is True
    svc.update_option(low["option_id"], option_label="低风险", display_order=20, enabled=False)
    analysis = svc.get_analysis("K1","V1")
    assert analysis["values"][field["field_id"]] == "LOW"
    low2 = next(x for x in svc.list_field_definitions()[0]["options"] if x["option_value"]=="LOW")
    assert low2["option_label"] == "低风险"
    assert low2["enabled"] == 0
