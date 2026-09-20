"""Minimal business-side example: prepare payload in Control Plane, call normal /v1 API.

The business payload is opaque to the Mock service. In a real project, the same
base_url is supplied to the Runtime/provider configuration instead of adding
Mock-specific fields to the business request.
"""

import json
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"

scenario = {
    "scenario_key": "default",
    "payload": {
        "field_id": "pe_cycle",
        "status": "FOUND",
        "normalized_value": 3000,
        "unit": "cycles",
    },
    "behavior": {},
}
control = Request(
    f"{BASE}/__mock__/scenario",
    data=json.dumps(scenario).encode(),
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urlopen(control, timeout=2):
    pass

request_body = {
    "model": "mock-gpt",
    "messages": [
        {
            "role": "user",
            "content": "extract storage lifetime field",
        }
    ],
}
provider_request = Request(
    f"{BASE}/v1/chat/completions",
    data=json.dumps(request_body).encode(),
    method="POST",
    headers={
        "Authorization": "Bearer mock-key",
        "Content-Type": "application/json",
    },
)
with urlopen(provider_request, timeout=2) as response:
    result = json.loads(response.read().decode())

print(result["choices"][0]["message"]["content"])
