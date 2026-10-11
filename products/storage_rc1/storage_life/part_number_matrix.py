"""Source-backed Part Number matrix extraction for NAND Product Brief tables.

This is a deterministic structural adapter, not an AI fact generator. It accepts
only explicit rows whose part number, capacity and page layout appear together
on the same source-native line. Every result is still a PENDING Candidate.
"""
from __future__ import annotations

import re
from typing import Iterable


_MODEL = re.compile(r"\b(?P<part>(?:TC|TH)58[A-Z0-9]{8,20})\b", re.I)
_PAGE = re.compile(r"\(\s*(?P<data>\d{3,5})\s*\+\s*(?P<spare>\d{1,4})\s*\)\s*[x×]\s*8\b", re.I)
_CAPACITY = re.compile(r"\b(?P<gbits>\d{1,3})\s*G\b", re.I)


def source_scoped_nand_rows(
    pages: Iterable[tuple[int, str, str]], *, source_id: str, vendor: str, device_type: str
) -> list[dict]:
    """Read verbatim PDF/Markdown part-number rows without crossing table rows.

    Never substitutes a family aggregate for a particular orderable part. Rows
    that cannot unambiguously match their page/part/layout are ignored.
    """
    vendor_key = re.sub(r"[^a-z0-9]", "", str(vendor or "").casefold())
    if device_type != "NAND Flash" or vendor_key not in {"kioxia", "kioxiacorp", "kioxiacorporation"}:
        return []
    records: dict[tuple[str, str], dict] = {}
    for page, page_text, _method in pages:
        # The brief labels (2048+128)x8 as Page Size (bit); the x8 expresses
        # the number of bits in each data/spare byte, not a second page size.
        if not all(label in page_text.casefold() for label in ("part number", "page size", "capacity")):
            continue
        for raw_line in page_text.splitlines():
            line = raw_line.strip().strip("|").strip()
            part_match = _MODEL.search(line)
            if not part_match:
                continue
            part = part_match.group("part").upper()
            suffix = line[part_match.end():]
            layout = _PAGE.search(suffix)
            if not layout:
                continue
            pre_layout = suffix[:layout.start()]
            capacity = _CAPACITY.search(pre_layout)
            if capacity is None:
                continue
            data_bytes, spare_bytes = int(layout.group("data")), int(layout.group("spare"))
            gbits = int(capacity.group("gbits"))
            if not (512 <= data_bytes <= 32768 and 1 <= spare_bytes < data_bytes and 1 <= gbits <= 1024):
                continue
            # Repeated OCR/Markdown echoes with different values for one part
            # are ambiguous: the caller must not accept either value.
            key = (source_id, part)
            candidate_row = {
                "part": part, "capacity_gbit": gbits,
                "page_data_bytes": data_bytes, "page_spare_bytes": spare_bytes,
                "source_id": source_id, "source_page": int(page),
                "source_text": line[:600],
            }
            previous = records.get(key)
            if previous is not None and any(previous[k] != candidate_row[k] for k in
                ("capacity_gbit", "page_data_bytes", "page_spare_bytes")):
                records[key] = {"conflict": True}
            elif previous is None:
                records[key] = candidate_row
    return [v for v in records.values() if not v.get("conflict")]


def scoped_candidates(rows: list[dict], *, field_labels: dict[str, str]) -> list[dict]:
    """Reuse the existing Candidate/Evidence contract; never confirm a fact."""
    candidates = []
    for row in rows:
        src, page, quote, part = (
            row["source_id"], row["source_page"], row["source_text"], row["part"]
        )
        for field, value, unit, condition in (
            ("capacity", str(row["capacity_gbit"]), "Gbit", ""),
            ("page_size", str(row["page_data_bytes"]), "bytes",
             f"spare={row['page_spare_bytes']} bytes; source layout=({row['page_data_bytes']}+{row['page_spare_bytes']})x8 bits"),
        ):
            evidence = {
                "source_id": src, "source_page": page, "source_section": "Part Number / Product Brief table",
                "source_text": quote, "scope": part, "confidence": 0.95,
                "extraction_method": "source_table_part_number",
            }
            candidates.append({
                "canonical_name": field, "parameter_name": field_labels.get(field, field),
                "ai_value": value, "ai_unit": unit, "condition": condition,
                "scope": part, "source_id": src, "source_page": page,
                "source_section": evidence["source_section"], "source_text": quote,
                "confidence": 0.95, "extraction_method": "source_table_part_number",
                "evidence": [evidence],
            })
    return candidates
