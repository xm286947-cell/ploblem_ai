"""Company-local staged file store for Hardware Tree imports.

Only opaque job directories and the validated original basename are used. The
absolute server path is never returned through the product API.
"""
from __future__ import annotations

from pathlib import Path

from services.hardware_tree_import_contract import (
    HardwareTreeImportContractError,
    validate_source_filename,
)


class HardwareTreeImportFileStore:
    def __init__(self, root: str | Path, *, max_bytes: int = 25 * 1024 * 1024):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(max_bytes)

    def save(self, job_id: str, filename: str, payload: bytes) -> Path:
        filename = validate_source_filename(filename)
        if Path(filename).suffix.lower() not in {".xlsx", ".xlsm"}:
            raise HardwareTreeImportContractError("EXCEL_FORMAT_UNSUPPORTED")
        if len(payload) <= 0:
            raise HardwareTreeImportContractError("EXCEL_FILE_EMPTY")
        if len(payload) > self.max_bytes:
            raise HardwareTreeImportContractError("EXCEL_FILE_TOO_LARGE")
        job_dir = self.root / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / filename
        temp = job_dir / (filename + ".part")
        temp.write_bytes(payload)
        temp.replace(target)
        return target

    def get(self, job_id: str, filename: str) -> Path:
        filename = validate_source_filename(filename)
        target = self.root / str(job_id) / filename
        if not target.is_file():
            raise HardwareTreeImportContractError("STAGED_EXCEL_NOT_FOUND")
        return target

    def delete(self, job_id: str, filename: str) -> None:
        target = self.root / str(job_id) / validate_source_filename(filename)
        if target.exists():
            target.unlink()
        parent = target.parent
        try:
            parent.rmdir()
        except OSError:
            pass
