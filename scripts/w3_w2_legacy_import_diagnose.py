"""Explain legacy W2 word-snapshot HTTP 400 using unmodified frozen fixtures."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import runpy
import sys

product=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(product))
target=runpy.run_path(str(product/"tests/test_hardware_r1_word_import_web.py"))
with TemporaryDirectory(prefix="w2_legacy_diag_") as td:
    root=Path(td)
    source=root/"A12345-Flash启动异常.docx"
    data=target["_docx"](source)
    client=target["_client"](root)
    endpoint="/api/v2/hardware-cases/r1/word-snapshot"
    request={"files":{"file":(source.name,data,
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}}
    res=client.post(endpoint,headers=target["MAINTAINER"],**request)
    safe={"status":res.status_code,"detail":res.json().get("detail"),
          "fixture_filename":source.name,
          "source_binding":res.json().get("source_binding",{}).get("binding_status"),
          "api_route":endpoint,
          "product_source_sha":"c7935e00f1f0abe828b0090524bb14ba923f3635"}
    print("W2_FROZEN_WORD_SNAPSHOT_DIAG="+json.dumps(safe,ensure_ascii=False))
    if res.status_code==400:
        print("W2_STATUS=LEGACY_RED_REPRODUCED")
    else:
        print("W2_STATUS=NEEDS_RECLASSIFICATION")
