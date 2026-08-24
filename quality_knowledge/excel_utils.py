from __future__ import annotations

def repair_read_only_dimensions(worksheet) -> tuple[str,str]:
    """Recalculate a read-only sheet range instead of trusting bad XLSX metadata."""
    try: before=worksheet.calculate_dimension(force=True)
    except TypeError: before=worksheet.calculate_dimension()
    if hasattr(worksheet,'reset_dimensions'):
        worksheet.reset_dimensions(); after=worksheet.calculate_dimension(force=True)
    else: after=before
    return before,after
