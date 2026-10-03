#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
INPUT="data/r1_field_validation/input_word"
OUTPUT="data/r1_field_validation/output_snapshot"
mkdir -p "$OUTPUT"
found=0
for f in "$INPUT"/*.docx; do
  [ -e "$f" ] || continue
  found=1
  name="$(basename "$f" .docx)"
  python3 scripts/hardware_r1_docx_snapshot.py "$f" --output "$OUTPUT/$name.json"
done
[ "$found" -eq 1 ] || { echo "NO_DOCX_IN_$INPUT"; exit 2; }
echo "HARDWARE_R1_6DOC_FIELD_VALIDATION_PARSE=PASS"
