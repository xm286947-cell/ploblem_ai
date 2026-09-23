from __future__ import annotations

import re


ITR_PERIOD_RE = re.compile(r"^ITR(\d{4})(\d{2})(\d{2})", re.IGNORECASE)


def parse_itr_period(issue_id: str) -> tuple[str, str]:
    """Return the submission year/month encoded in ITRYYYYMMDD... ."""
    match = ITR_PERIOD_RE.match(str(issue_id or "").strip())
    if not match:
        return "", ""
    year, month, _day = match.groups()
    month_no = int(month)
    if not 1 <= month_no <= 12:
        return year, ""
    return year, f"{month_no}月"


def normalize_year(value: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{4}", value):
        raise ValueError("年份必须为4位数字")
    return value


def normalize_month(value: str) -> str:
    value = str(value or "").strip().replace("月份", "").replace("月", "")
    if not value.isdigit() or not 1 <= int(value) <= 12:
        raise ValueError("月份必须为1至12")
    return f"{int(value)}月"
