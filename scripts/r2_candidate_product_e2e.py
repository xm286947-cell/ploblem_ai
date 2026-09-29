from __future__ import annotations

import argparse
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def _json(url: str, *, timeout: float = 15.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _multipart(
    url: str,
    *,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    timeout: float = 180.0,
) -> Any:
    boundary = "----r2candidate" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                ).encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(
        url,
        data=b"".join(chunks),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP_{exc.code}:{url}:{body}"
        ) from exc


def _identity_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "、".join(
            item
            for item in (_identity_value(x) for x in value)
            if item
        )
    if isinstance(value, dict):
        for key in (
            "value",
            "text",
            "name",
            "model",
            "part_number",
            "code",
            "raw_value",
        ):
            if key in value:
                text = _identity_value(value[key])
                if text:
                    return text
        return ""
    return str(value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--provider-log", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    pdf = (
        args.package_root
        / "products"
        / "storage_rc1"
        / "examples"
        / "synthetic_emmc.pdf"
    )
    _require(pdf.is_file(), f"PACKAGE_PDF_FIXTURE_MISSING:{pdf}")

    release = _json(base + "/storage-workspace/api/product/knowledge/status")
    _require(release.get("available") is True, f"FORMAL_RELEASE_NOT_LOADED:{release}")
    _require(
        release.get("knowledge_release_version") == "KP-STORAGE-RC1-VALIDATION-001",
        f"FORMAL_RELEASE_VERSION_UNEXPECTED:{release}",
    )
    print("FORMAL_KNOWLEDGE_RELEASE_LOADED_ON_CLEAN_START=PASS")
    print(
        "FORMAL_KNOWLEDGE_RELEASE_VERSION="
        + str(release.get("knowledge_release_version"))
    )

    runtime = _json(base + "/storage-workspace/api/v1/runtime/status")
    _require(runtime.get("configured") is True, f"STORAGE_RUNTIME_NOT_CONFIGURED:{runtime}")
    _require(runtime.get("profile") == "qwen_prod", f"MODEL_REF_NOT_QWEN_PROD:{runtime}")
    _require(runtime.get("api_key_env") == "acca1", f"SECRETREF_NOT_ACCA1:{runtime}")
    _require(runtime.get("api_key_present") is True, f"SECRETREF_NOT_PRESENT:{runtime}")
    print("SECRETREF_CHILD_PROCESS_RESOLUTION=PASS")
    print("SECRETREF_NAME=acca1")
    print("SECRETREF_PRESENT=PRESENT")

    identified = _multipart(
        base + "/storage-workspace/api/documents/identify",
        fields={},
        file_field="file",
        file_path=pdf,
        timeout=180.0,
    )
    vendor = _identity_value(identified.get("vendor"))
    model = _identity_value(identified.get("model"))
    device_type = _identity_value(identified.get("device_type"))
    _require(vendor == "Demo Storage", f"PDF_IDENTITY_VENDOR_FAIL:{identified}")
    _require(model == "SYN-EMMC-1", f"PDF_IDENTITY_MODEL_FAIL:{identified}")
    _require(device_type == "eMMC", f"PDF_IDENTITY_TYPE_FAIL:{identified}")
    _require(
        "[object Object]" not in json.dumps(identified, ensure_ascii=False),
        "PDF_IDENTITY_OBJECT_OBJECT_RENDER_RISK",
    )
    print("PDF_IDENTIFY=PASS")
    print(f"PDF_IDENTITY={vendor}/{model}/{device_type}")

    doc_identity = identified.get("document_identity") or {}
    fields = {
        "vendor": vendor,
        "model": model,
        "device_type": device_type,
        "original_url": "",
        "publisher": "R2 Candidate E2E",
        "models_json": json.dumps(
            identified.get("models") or [],
            ensure_ascii=False,
        ),
        "document_number": _identity_value(doc_identity.get("document_number")),
        "revision": _identity_value(doc_identity.get("revision")),
        "revision_date": _identity_value(doc_identity.get("revision_date")),
        "document_variant": _identity_value(doc_identity.get("document_variant")),
    }
    created = _multipart(
        base + "/storage-workspace/api/documents/jobs",
        fields=fields,
        file_field="file",
        file_path=pdf,
        timeout=30.0,
    )
    job_id = str(created.get("job_id") or "")
    _require(job_id, f"PDF_JOB_CREATE_FAIL:{created}")
    print("PDF_JOB_CREATED=PASS")
    print("PDF_JOB_ID=" + job_id)

    deadline = time.monotonic() + args.timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _json(
            base + "/storage-workspace/api/documents/jobs/" + job_id,
            timeout=20.0,
        )
        status = str(last.get("status") or "").lower()
        print(
            "PDF_JOB_PROGRESS="
            + str(last.get("progress"))
            + ":"
            + str(last.get("stage"))
            + ":"
            + status
        )
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError("PDF_JOB_FAILED:" + json.dumps(last, ensure_ascii=False))
        time.sleep(1.0)
    else:
        raise RuntimeError("PDF_JOB_TIMEOUT:" + json.dumps(last, ensure_ascii=False))

    result = last.get("result") or {}
    device_id = str(result.get("device_id") or "")
    _require(device_id, f"PDF_JOB_DEVICE_MISSING:{last}")
    coverage = result.get("coverage") or {}
    states = coverage.get("states") or []
    _require(len(states) > 0, f"COVERAGE_NOT_PRODUCED:{result}")
    print("PDF_JOB_COMPLETED=PASS")
    print("COVERAGE=PASS")
    print("COVERAGE_STATE_COUNT=" + str(len(states)))
    print("REVIEW_REQUIRED=" + ("YES" if result.get("review_required") else "NO"))

    review = _json(
        base
        + "/storage-workspace/api/product/devices/"
        + device_id
        + "/review-workbench"
    )
    rows = review.get("rows") if isinstance(review, dict) else None
    _require(isinstance(rows, list), f"REVIEW_WORKBENCH_INVALID:{review}")
    _require(len(rows) > 0, f"REVIEW_WORKBENCH_EMPTY:{review}")
    print("REVIEW_WORKBENCH=PASS")
    print("REVIEW_ROW_COUNT=" + str(len(rows)))

    if not args.provider_log.is_file():
        raise RuntimeError("PROVIDER_LOG_MISSING")
    events = [
        json.loads(line)
        for line in args.provider_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _require(events, "PROVIDER_REQUEST_NOT_OBSERVED")
    _require(
        all(item.get("authorization") == "PRESENT" for item in events),
        f"PROVIDER_AUTH_HEADER_ABSENT:{events}",
    )
    _require(
        not any(item.get("contains_acca1_value") for item in events),
        "PROVIDER_LOG_SECRET_LEAK",
    )
    print("STORAGE_AI_HTTP_PROVIDER_CALL=PASS")
    print("PROVIDER_AUTHORIZATION=PRESENT")
    print("PROVIDER_REQUEST_COUNT=" + str(len(events)))

    print("PDF_E2E_FROM_BUILT_ZIP=PASS")
    print("SECRETREF_CHILD_PROCESS_E2E=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
