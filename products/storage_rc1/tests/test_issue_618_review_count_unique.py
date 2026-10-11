"""The Golden A review summary counts candidate IDs, not duplicated field rows."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


def test_golden_a_review_counter_deduplicates_candidate_alias_rows():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the browser counter function")
    html = (Path(__file__).resolve().parents[1] / "storage_life" / "index.html").read_text(encoding="utf-8")
    func = re.search(r"^function syncGoldenAReview\(\)\{.*\}$", html, re.MULTILINE)
    assert func is not None, "Golden A review counter function missing"
    script = """
const assert=require('node:assert/strict');
let REVIEW, state={};
function setGoldenAStage(k,s,d){state[k]={status:s,detail:d};}
""" + func.group(0) + """
const rows=Array.from({length:20},(_,i)=>({candidate_id:'C'+i,review_status:'UNREVIEWED',evidence:[{source_page:4}]}));
rows.push({...rows[3]}); // One ECC Candidate appears twice via requirement/observability aliases.
REVIEW={rows};syncGoldenAReview();
assert.equal(state.REVIEW.detail,'可审核 20 · 已确认 0');
assert.equal(state.EVIDENCE.detail,'有证据参数 20');
rows[3].review_status='CONFIRMED';rows[20].review_status='CONFIRMED';syncGoldenAReview();
assert.equal(state.REVIEW.detail,'可审核 20 · 已确认 1');
REVIEW={rows:[{candidate_id:null,review_status:'NOT_REVIEWED',evidence:[]}]};syncGoldenAReview();
assert.equal(state.REVIEW.detail,'可审核 0 · 已确认 0');
assert.equal(state.EVIDENCE.status,'WAITING');
"""
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
