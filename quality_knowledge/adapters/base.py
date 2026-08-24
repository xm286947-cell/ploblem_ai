from __future__ import annotations
import hashlib, json, uuid
from pathlib import Path
from typing import Any
from quality_knowledge.models.issue import *
from quality_knowledge.config_loader import normalize_header, issue_id_aliases

def clean(v: Any) -> str:
    if v is None: return ""
    return str(v).strip()

def clean_header(v: Any) -> str:
    return normalize_header(v)

def pick(row: dict[str, Any], *names: str) -> str:
    # row keys are normalized during import; normalize lookup names too.
    for n in names:
        key = normalize_header(n)
        if key in row and clean(row[key]):
            return clean(row[key])
    return ""

class BaseIssueAdapter:
    business_type = ""
    issue_id_fields: tuple[str, ...] = ()
    def adapt(self, row: dict[str, Any], *, source_file="", source_sheet="", source_row=None, batch_id="") -> QualityIssueDTO:
        raw_original = {clean(k): ("" if v is None else v) for k,v in row.items() if clean(k)}
        raw = {normalize_header(k): v for k,v in raw_original.items() if normalize_header(k)}
        if getattr(self, "mapping_config", None) is not None:
            from quality_knowledge.mapping.runtime import runtime_aliases
            aliases = runtime_aliases(self.mapping_config)
        else:
            aliases = issue_id_aliases(self.business_type) or tuple(normalize_header(x) for x in self.issue_id_fields)
        issue_id = pick(raw, *aliases)
        if not issue_id:
            issue_id = f"ROW-{source_row or uuid.uuid4().hex[:12]}"
        raw_serialized = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        source_hash = hashlib.sha256(raw_serialized.encode("utf-8")).hexdigest()
        knowledge_id = f"{self.business_type}-{hashlib.sha1((issue_id+'|'+source_hash).encode()).hexdigest()[:16]}"
        dto = self.build(raw, issue_id, knowledge_id, source_hash, source_file, source_sheet, source_row, batch_id)
        # Frozen design: raw_record must preserve original Excel headers exactly as read.
        dto.raw_record = raw_original
        return dto
    def build(self, row, issue_id, knowledge_id, source_hash, source_file, source_sheet, source_row, batch_id):
        raise NotImplementedError
    def common(self, row, issue_id, knowledge_id, source_hash, source_file, source_sheet, source_row, batch_id, *, fact, context, occurrence, escape, solution, verification=None, extension=None):
        return QualityIssueDTO(
            identity=IssueIdentity(knowledge_id=knowledge_id, case_id=knowledge_id, business_type=self.business_type, issue_id=issue_id, source_record_id=f"{self.business_type}:{issue_id}"),
            source=IssueSource(source_file=Path(source_file).name if source_file else "", source_sheet=source_sheet, source_row=source_row, source_import_batch=batch_id, raw_record_ref=f"{Path(source_file).name}:{source_sheet}:{source_row}", source_hash=source_hash),
            issue_fact=fact, product_context=context, occurrence=occurrence, escape=escape, solution=solution,
            verification=verification or VerificationFact(), product_extension=extension or {}, raw_record=row,
        )
