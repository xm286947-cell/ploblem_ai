from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "config" / "spec_templates.yaml"
KNOWLEDGE_PATH = ROOT / "config" / "parameter_knowledge.yaml"


@lru_cache(maxsize=1)
def load_templates():
    raw = yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw.get("device_types"), dict):
        raise ValueError("spec_templates.yaml 缺少 device_types")
    return raw


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def normalize_device_type(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return value
    config = load_templates()
    marker = _norm(value)
    for canonical, item in config["device_types"].items():
        aliases = [canonical] + list(item.get("aliases") or [])
        if marker in {_norm(x) for x in aliases}:
            return canonical
    return value


def device_types():
    return list(load_templates()["device_types"].keys())


def fields_for(device_type: str) -> dict[str, str]:
    canonical = normalize_device_type(device_type)
    item = load_templates()["device_types"].get(canonical)
    if not item:
        raise ValueError("不支持的器件类型")
    return dict(item.get("fields") or {})




def analysis_fields_for(device_type: str) -> list[str]:
    canonical = normalize_device_type(device_type)
    item = load_templates()["device_types"].get(canonical)
    if not item:
        raise ValueError("不支持的器件类型")
    fields = fields_for(canonical)
    requested = list(item.get("analysis_fields") or fields.keys())
    return [x for x in requested if x in fields]

def parameter_label(device_type: str, canonical_name: str, fallback: str = "") -> str:
    """Return the user-facing Chinese (English) field label without changing canonical keys."""
    try:
        return fields_for(device_type).get(canonical_name) or fallback or canonical_name
    except ValueError:
        return fallback or canonical_name


def display_status(value: str) -> str:
    return {
        "pending": "待确认（Pending）",
        "confirmed": "已确认（Confirmed）",
        "rejected": "已驳回（Rejected）",
    }.get(str(value or "").strip().lower(), str(value or ""))


def display_condition(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw).strip().lower()
    exact = {
        "typ": "典型值（Typ）", "typical": "典型值（Typical）",
        "min": "最小值（Min）", "minimum": "最小值（Minimum）",
        "max": "最大值（Max）", "maximum": "最大值（Maximum）",
        "with ecc": "启用ECC（With ECC）",
        "ecc on": "ECC开启（ECC On）", "ecc off": "ECC关闭（ECC Off）",
        "internal ecc enabled": "内部ECC开启（Internal ECC Enabled）",
        "internal ecc disabled": "内部ECC关闭（Internal ECC Disabled）",
        "industrial": "工业级（Industrial）", "industrial+": "工业增强级（Industrial+）",
    }
    if key in exact:
        return exact[key]
    # Preserve compound meanings while translating common tokens.
    out = raw
    replacements = [
        (r"\bECC\s+on\b", "ECC开启（ECC On）"),
        (r"\bECC\s+off\b", "ECC关闭（ECC Off）"),
        (r"\btypical\b", "典型值（Typical）"),
        (r"\bMin\b", "最小值（Min）"),
        (r"\bMax\b", "最大值（Max）"),
    ]
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    return out


def display_scope(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.casefold() == "product family":
        return "产品族（Product Family）"
    return raw


def _dedupe_value_unit(value: str, unit: str) -> tuple[str, str]:
    value = re.sub(r"\s+", " ", str(value or "").strip())
    unit = re.sub(r"\s+", " ", str(unit or "").strip())
    # Normalize common engineering typography.
    value = re.sub(r"(?i)(\d)\s*u?s\b", lambda m: m.group(0), value)
    value = re.sub(r"(?i)(\d+)\s*us\b", r"\1 μs", value)
    value = re.sub(r"(?i)(\d+(?:\.\d+)?)\s*ms\b", r"\1 ms", value)
    if unit.lower() in {"us", "µs", "μs"}:
        unit = "μs"
    if unit and re.search(r"(?:^|\s)" + re.escape(unit) + r"$", value, flags=re.IGNORECASE):
        unit = ""
    # AI sometimes returns 400us + unit=us or 1Gb + unit=Gb/bit.
    compact_value = re.sub(r"[^a-z0-9μ]+", "", value.casefold())
    compact_unit = re.sub(r"[^a-z0-9μ]+", "", unit.casefold())
    if unit and compact_unit and compact_value.endswith(compact_unit):
        unit = ""
    return value, unit


def format_spec_value(canonical_name: str, value: str, unit: str = "") -> str:
    """Normalize only presentation; source/raw values remain untouched for traceability."""
    raw_value = str(value or "").strip()
    raw_unit = str(unit or "").strip()
    if canonical_name in {"nand_type", "cell_type", "default_user_area_type", "enhanced_area_cell_type"}:
        marker = f"{raw_value} {raw_unit}".upper()
        for token, label in (
            ("PSLC", "伪SLC（pSLC）"),
            ("SLC", "单层单元（SLC）"),
            ("MLC", "多层单元（MLC）"),
            ("TLC", "三层单元（TLC）"),
            ("QLC", "四层单元（QLC）"),
        ):
            if re.search(rf"\b{token}\b", marker):
                return label
    if canonical_name == "capacity":
        combined = f"{raw_value} {raw_unit}".strip()
        # Preserve GB (bytes) for SSD/eMMC; normalize Gb/Gbit variants as gigabits.
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:G[- ]?bit|Gbit|Gb|G\s*bit|G)\s*(?:bit|Gb|Gbit)?\s*", combined, re.IGNORECASE)
        if m and not re.search(r"\bGB\b", combined):
            return f"{m.group(1)} Gbit"
    value2, unit2 = _dedupe_value_unit(raw_value, raw_unit)
    if canonical_name == "pe_cycles" and unit2.lower() in {"cycle", "cycles"}:
        return f"{value2} 次（cycles）"
    return f"{value2} {unit2}".strip()

def vendor_key(vendor: str) -> str | None:
    marker = _norm(vendor)
    if not marker:
        return None
    for key, item in (load_templates().get("vendors") or {}).items():
        aliases = [item.get("canonical_name", key)] + list(item.get("aliases") or [])
        if marker in {_norm(x) for x in aliases}:
            return key
    return None


def canonical_vendor(vendor: str) -> str:
    key = vendor_key(vendor)
    if not key:
        return str(vendor or "").strip()
    return str(load_templates()["vendors"][key].get("canonical_name") or vendor).strip()


def supported_vendor_template(vendor: str, device_type: str) -> bool:
    key = vendor_key(vendor)
    if not key:
        return False
    dtype = normalize_device_type(device_type)
    return dtype in set(load_templates()["vendors"][key].get("supported_types") or [])


def section_groups(device_type: str, vendor: str = ""):
    dtype = normalize_device_type(device_type)
    config = load_templates()
    groups = [dict(x) for x in config.get("generic_section_groups", {}).get(dtype, [])]
    key = vendor_key(vendor)
    if key:
        groups.extend(dict(x) for x in config["vendors"][key].get("overrides", {}).get(dtype, []))
    return groups


def _contains_heading(text: str, heading: str) -> bool:
    # Section navigation only: this is deliberately not a value-extraction rule.
    hay = " ".join(str(text or "").upper().split())
    needle = " ".join(str(heading or "").upper().split())
    return bool(needle and needle in hay)


def build_read_plan(pages, device_type: str, vendor: str = ""):
    """Return high-value pages/field groups without extracting any parameter values.

    The plan uses only section names/headings. Parameter values are still extracted by the Agent.
    If a field has no mapped section, it is picked up by a second missing-field pass in ai.py.
    """
    dtype = normalize_device_type(device_type)
    fields = fields_for(dtype)
    groups = section_groups(dtype, vendor)
    page_map = {p[0]: p for p in pages if p[1].strip()}
    if not page_map:
        return []

    planned = {}
    for page, text, method in page_map.values():
        # Headings are normally at the beginning of a page, but TOC and table pages can be useful too.
        head = text[:2400]
        for rank, group in enumerate(groups):
            headings = list(group.get("headings") or [])
            matched = [h for h in headings if _contains_heading(head, h)]
            if not matched:
                continue
            entry = planned.setdefault(page, {"page": page, "method": method, "sections": [], "fields": set(), "priority": 999})
            entry["sections"].append(matched[0])
            entry["fields"].update(f for f in group.get("fields") or [] if f in fields)
            entry["priority"] = min(entry["priority"], rank)
            # Most datasheet sections span more than one page. Vendors may override the span
            # (GigaDevice Parameter Page is a multi-page table, for example).
            following = int(group.get("following_pages", 1) or 0)
            for offset in range(1, following + 1):
                next_page = page + offset
                if next_page not in page_map:
                    continue
                nxt = planned.setdefault(next_page, {"page": next_page, "method": page_map[next_page][2], "sections": [], "fields": set(), "priority": 999})
                nxt["sections"].append(f"continued:{matched[0]}")
                nxt["fields"].update(f for f in group.get("fields") or [] if f in fields)
                nxt["priority"] = min(nxt["priority"], rank + 20 + offset)

    # Always keep the first six text pages as overview/identity evidence, but target only overview-style fields.
    overview_fields = [f for f in ("capacity", "cell_type", "nand_type", "pe_cycles", "retention", "life_time_a", "life_time_b", "pre_eol", "smart_health") if f in fields]
    for page in sorted(page_map)[:6]:
        entry = planned.setdefault(page, {"page": page, "method": page_map[page][2], "sections": [], "fields": set(), "priority": 999})
        entry["sections"].append("document_overview")
        entry["fields"].update(overview_fields)
        entry["priority"] = min(entry["priority"], 50)

    # Short/synthetic datasheets often have no formal section headings. In that case scan
    # their few pages with the full field set rather than losing fields solely because a heading is absent.
    if len(page_map) <= 8:
        for page in sorted(page_map):
            entry = planned.setdefault(page, {"page": page, "method": page_map[page][2], "sections": [], "fields": set(), "priority": 999})
            entry["sections"].append("short_document_fallback")
            entry["fields"].update(analysis_fields_for(dtype))
            entry["priority"] = min(entry["priority"], 80)

    out = []
    for page in sorted(planned, key=lambda p: (planned[p]["priority"], p)):
        entry = planned[page]
        if not entry["fields"]:
            continue
        out.append({
            "page": page,
            "method": entry["method"],
            "section": " / ".join(dict.fromkeys(entry["sections"])),
            "target_fields": sorted(entry["fields"]),
            "priority": entry["priority"],
        })
    return out



@lru_cache(maxsize=1)
def load_parameter_knowledge():
    raw = yaml.safe_load(KNOWLEDGE_PATH.read_text(encoding="utf-8")) or {}
    return raw

def parameter_knowledge(device_type: str, canonical_name: str) -> dict:
    dtype = normalize_device_type(device_type)
    return dict((((load_parameter_knowledge().get("device_types") or {}).get(dtype) or {}).get("parameters") or {}).get(canonical_name) or {})

def lifetime_profile(device_type: str) -> dict:
    dtype = normalize_device_type(device_type)
    item = ((load_parameter_knowledge().get("device_types") or {}).get(dtype) or {})
    return {"device_type": dtype, "operation_model": item.get("operation_model", ""), "parameters": dict(item.get("parameters") or {})}

def template_summary(device_type: str, vendor: str = ""):
    dtype = normalize_device_type(device_type)
    key = vendor_key(vendor)
    return {
        "device_type": dtype,
        "vendor": canonical_vendor(vendor),
        "vendor_template": key,
        "vendor_template_supported": supported_vendor_template(vendor, dtype),
        "fields": [{"canonical_name": k, "parameter_name": v, **parameter_knowledge(dtype, k)} for k, v in fields_for(dtype).items()],
        "lifetime_profile": lifetime_profile(dtype),
        "analysis_fields": analysis_fields_for(dtype),
        "section_groups": [
            {"name": g.get("name"), "headings": list(g.get("headings") or []), "fields": list(g.get("fields") or [])}
            for g in section_groups(dtype, vendor)
        ],
    }
