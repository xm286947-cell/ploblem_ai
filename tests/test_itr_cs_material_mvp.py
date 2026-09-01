import json
import sqlite3

from openpyxl import Workbook
from fastapi.testclient import TestClient

from quality_knowledge.materials import MaterialImportService, MaterialRepository, combine_headers, normalize_itr
from quality_knowledge.web.app import create_app


def workbook(path, material_type, key):
    book=Workbook();sheet=book.active
    sheet.append(["问题信息", "问题信息", "问题信息"])
    sheet.append(["彻底解决单号" if material_type=="ITR_CS" else "ITR单号", "产品类型", "问题描述"])
    sheet.append([key, "PLC", "测试问题"]);book.save(path)


def operations_workbook(path, key):
    book=Workbook();sheet=book.active
    sheet.append(["数据运营","数据运营","问题信息","问题信息","技术根因分析与纠正"])
    sheet.append(["审核状态","KPI计入月份","彻底解决单号","问题描述","软件模块"])
    sheet.append(["已审核","2026-06",key,"软件异常","通信模块"]);book.save(path)


def test_two_row_headers_and_itr_normalization():
    assert combine_headers(("问题信息", None), ("ITR单号", "产品类型")) == ["问题信息_ITR单号", "问题信息_产品类型"]
    assert normalize_itr(" itr20260605084cs ") == "ITR20260605084"


def test_group_isolation_versioning_and_linking(tmp_path):
    db=tmp_path/"app.db";repo=MaterialRepository(db)
    with repo.connect() as c:
        c.execute("CREATE TABLE quality_issue(knowledge_id TEXT PRIMARY KEY,business_issue_id TEXT)")
        c.execute("INSERT INTO quality_issue VALUES('K-1','ITR20260605084')")
    itr=tmp_path/"itr.xlsx";cs=tmp_path/"cs.xlsx"
    workbook(itr,"ITR_SOURCE","ITR20260605084");workbook(cs,"ITR_CS","ITR20260605084CS")
    service=MaterialImportService(repo)
    assert service.import_file(itr,"ITR",2)["new"] == 1
    assert service.import_file(cs,"ITR-CS",2)["new"] == 1
    assert service.import_file(cs,"ITR-CS",2)["skipped"] == 1
    assert len(repo.list_materials("ITR")) == 1
    assert len(repo.list_materials("ITR-CS")) == 1
    linked=repo.materials_for_issue("K-1")
    assert {x["material_type"] for x in linked} == {"ITR_SOURCE","ITR_CS"}
    assert all(x["canonical_itr"]=="ITR20260605084" for x in linked)
    assert json.dumps(linked,ensure_ascii=False).find("测试问题") >= 0


def test_default_groups_do_not_enter_ai_or_insight(tmp_path):
    repo=MaterialRepository(tmp_path/"app.db")
    groups={x["material_type"]:x for x in repo.groups(False)}
    assert groups["ESCAPE_ANALYSIS"]["include_ai"] == 1
    assert groups["ITR_SOURCE"]["include_ai"] == 0
    assert groups["ITR_CS"]["include_insight"] == 0
    assert groups["SOFTWARE_OPERATION"]["include_report"] == 0


def test_material_web_import_and_navigation(tmp_path):
    db=tmp_path/"web.db";client=TestClient(create_app(db))
    assert client.get("/materials",follow_redirects=False).headers["location"]=="/materials/itr"
    page=client.get("/materials/itr")
    assert page.status_code==200 and "ITR问题工作台" in page.text and "两级表头" in page.text
    assert "软件 / 硬件 / 机械 / 跨领域" in page.text
    assert "仅软件" in client.get("/materials/software-operations").text
    itr=tmp_path/"itr.xlsx";workbook(itr,"ITR_SOURCE","ITR20260605084")
    with itr.open("rb") as stream:
        result=client.post("/materials/import",data={"workbench":"itr","group_code":"ITR","header_rows":"2"},files={"file":("itr.xlsx",stream,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert result.status_code==200 and "ITR20260605084" in result.text and "新增 1" in result.text
    assert "ITR工作台" in client.get("/issues").text
    settings=client.get("/settings/associations")
    assert settings.status_code==200 and "关联预检" in settings.text and "移除末尾CS" in settings.text
    saved=client.post("/settings/associations/RULE-CS-ITR",data={"source_field":"问题信息_彻底解决单号","target_field":"问题信息_ITR单号","transform":"STRIP_CS","status":"INACTIVE"},follow_redirects=False)
    assert saved.status_code==303
    assert next(x for x in client.app.state.material_repository.rules() if x["rule_id"]=="RULE-CS-ITR")["status"]=="INACTIVE"


def test_disabled_rule_stops_automatic_link_but_keeps_other_type(tmp_path):
    db=tmp_path/"rules.db";repo=MaterialRepository(db)
    with repo.connect() as c:
        c.execute("CREATE TABLE quality_issue(knowledge_id TEXT PRIMARY KEY,business_issue_id TEXT)")
        c.execute("INSERT INTO quality_issue VALUES('K-1','ITR20260605084')")
    itr=tmp_path/"itr.xlsx";cs=tmp_path/"cs.xlsx";workbook(itr,"ITR_SOURCE","ITR20260605084");workbook(cs,"ITR_CS","ITR20260605084CS")
    service=MaterialImportService(repo);service.import_file(itr,"ITR",2);service.import_file(cs,"ITR-CS",2)
    repo.update_rule("RULE-CS-ITR",source_field="问题信息_彻底解决单号",target_field="问题信息_ITR单号",transform="STRIP_CS",status="INACTIVE");repo.refresh_links()
    assert {x["material_type"] for x in repo.materials_for_issue("K-1")}=={"ITR_SOURCE"}


def test_software_operation_is_isolated_and_links_by_cs_number(tmp_path):
    db=tmp_path/"ops.db";repo=MaterialRepository(db)
    with repo.connect() as c:
        c.execute("CREATE TABLE quality_issue(knowledge_id TEXT PRIMARY KEY,business_issue_id TEXT)")
        c.execute("INSERT INTO quality_issue VALUES('K-OPS','ITR20260605084')")
    source=tmp_path/"ops.xlsx";operations_workbook(source,"ITR20260605084CS")
    stats=MaterialImportService(repo).import_file(source,"SW-OPS",2)
    assert stats["new"]==1
    material=repo.list_materials("SW-OPS")[0]
    assert material["material_type"]=="SOFTWARE_OPERATION" and material["canonical_itr"]=="ITR20260605084"
    linked=repo.materials_for_issue("K-OPS")
    assert linked[0]["raw"]["数据运营_KPI计入月份"]=="2026-06"
