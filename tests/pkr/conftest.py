from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PKR_DATA_DIR", tempfile.mkdtemp(prefix="pkr-tests-bootstrap-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
