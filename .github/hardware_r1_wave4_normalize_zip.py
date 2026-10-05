from __future__ import annotations

import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path


ARCHIVE = Path(sys.argv[1])


def normalize(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def payload_digest(entries: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(entries):
        if name in {
            "PACKAGE_MANIFEST.json",
            "PACKAGE_GATE_REPORT.json",
            "WAVE4_VALIDATION_MANIFEST.json",
        }:
            continue
        digest.update(name.encode())
        digest.update(entries[name])
    return digest.hexdigest()


with zipfile.ZipFile(ARCHIVE) as source:
    entries = {name: normalize(source.read(name)) for name in source.namelist()}

manifest = json.loads(entries["PACKAGE_MANIFEST.json"].decode("utf-8"))
manifest["package_sha256"] = payload_digest(entries)


def write_archive() -> None:
    entries["PACKAGE_MANIFEST.json"] = (
        json.dumps(manifest, indent=2) + "\n"
    ).encode("utf-8")
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            mode = 0o755 if name.endswith(".command") else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            output.writestr(info, entries[name])


write_archive()
for _ in range(3):
    manifest["package_size"] = ARCHIVE.stat().st_size
    write_archive()
