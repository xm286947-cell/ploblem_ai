from __future__ import annotations

import argparse
import json
import mimetypes
import time
import uuid
from pathlib import Path
from urllib import request

EXPECTED_AGENT = "storage.emmc.parameter_extract"
EXPECTED_BUDGET = 2


def http_json(url: str, method: str = "GET", body=None, headers=None, timeout: float = 20.0):
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode()
            headers = {**(headers or {}), "Content-Type": "application/json"}
        else:
            data = body
    req = request.Request(url, data=data, method=method, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def multipart(fields: dict[str, str], file_path: Path):
    boundary = "----StorageFailE2E" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/pdf"
    body = bytearray()
    for key, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
        ).encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode()
    body += file_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--pdf", default="examples/synthetic_emmc.pdf")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    pdf = Path(a.pdf)
    if not pdf.is_absolute():
        pdf = root / pdf
    base = a.base.rstrip("/")

    status, health = http_json(base + "/api/health")
    assert status == 200 and health.get("status") == "ok"

    # Metadata stages must remain healthy. The persistent 503 is targeted only at
    # the dedicated eMMC parameter-extraction agent so the web product reaches
    # the real Storage -> Runtime boundary before failing.
    body, headers = multipart({}, pdf)
    _, ident = http_json(base + "/api/documents/identify", "POST", body, headers, 60)
    fields = {
        "vendor": ident.get("vendor", {}).get("value") or "Demo Storage",
        "model": ident.get("model", {}).get("value") or "SYN-EMMC-1",
        "device_type": ident.get("device_type", {}).get("value") or "eMMC",
        "original_url": "",
        "publisher": "Demo",
        "models_json": json.dumps(ident.get("models") or [], ensure_ascii=False),
        "document_number": "",
        "revision": "",
        "revision_date": "",
        "document_variant": "",
    }
    body, headers = multipart(fields, pdf)
    _, job = http_json(base + "/api/documents/jobs", "POST", body, headers, 60)

    deadline = time.time() + a.timeout
    last_job = None
    while time.time() < deadline:
        _, last_job = http_json(base + "/api/documents/jobs/" + job["job_id"])
        if last_job.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.4)
    if not last_job or last_job.get("status") != "failed":
        raise SystemExit("EXPECTED_IMPORT_FAILURE_NOT_OBSERVED " + json.dumps(last_job, ensure_ascii=False))

    _, executions = http_json(base + "/api/v1/runtime/executions")
    items = executions.get("items") or []
    if not items:
        raise SystemExit("NO_RUNTIME_FAILURE_EVIDENCE")
    dedicated = [x for x in items if x.get("agent_id") == EXPECTED_AGENT]
    if not dedicated:
        raise SystemExit("DEDICATED_EMMC_RUNTIME_AGENT_NOT_REACHED")
    last = dedicated[-1]
    if last.get("status") != "FAILED":
        raise SystemExit("RUNTIME_DID_NOT_FAIL " + json.dumps(last, ensure_ascii=False))
    if int(last.get("provider_calls") or 0) != EXPECTED_BUDGET:
        raise SystemExit("HARD_BUDGET_NOT_ENFORCED " + json.dumps(last, ensure_ascii=False))
    if last.get("retry_budget_exhausted") is not True:
        raise SystemExit("RETRY_BUDGET_EXHAUSTED_NOT_REPORTED " + json.dumps(last, ensure_ascii=False))
    error_info = last.get("error") or {}
    if error_info.get("category") != "TRANSPORT":
        raise SystemExit("WRONG_ERROR_CATEGORY " + json.dumps(last, ensure_ascii=False))

    _, counters_obj = http_json("http://127.0.0.1:18000/__mock__/counters")
    counters = counters_obj.get("data") or {}
    mock_requests = sum(int(v) for v in counters.values())
    runtime_provider_calls = sum(int(x.get("provider_calls") or 0) for x in items)
    if mock_requests != runtime_provider_calls:
        raise SystemExit(
            f"HTTP_PROVIDER_ACCOUNTING_MISMATCH mock={mock_requests} runtime={runtime_provider_calls}"
        )

    # Product service must remain available after the business job fails.
    _, health_after = http_json(base + "/api/health")
    if health_after.get("status") != "ok":
        raise SystemExit("PRODUCT_SERVICE_NOT_HEALTHY_AFTER_PROVIDER_FAILURE")

    out = root / "release" / "PRODUCT_FAILURE_E2E_RESULT.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "health_before": health,
                "health_after": health_after,
                "import_job": last_job,
                "runtime_execution": last,
                "accounting": {
                    "mock_requests": mock_requests,
                    "runtime_provider_calls": runtime_provider_calls,
                    "match": True,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("PRODUCT FAILURE E2E PASS", out)


if __name__ == "__main__":
    main()
