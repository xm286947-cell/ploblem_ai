from __future__ import annotations

from dataclasses import dataclass

from scripts.hardware_r1_e2e_validation_start import _is_empty_consumption_search


@dataclass
class FakeResponse:
    status_code: int
    payload: object

    @property
    def text(self) -> str:
        return str(self.payload)

    def json(self):
        return self.payload


def test_first_install_consumption_search_accepts_current_empty_contract():
    response = FakeResponse(
        200,
        {
            "contract_version": "hardware-knowledge-consumption/v1",
            "results": [],
        },
    )

    assert _is_empty_consumption_search(response)


def test_first_install_consumption_search_rejects_wrong_status_or_contract():
    assert not _is_empty_consumption_search(
        FakeResponse(
            503,
            {
                "contract_version": "hardware-knowledge-consumption/v1",
                "results": [],
            },
        )
    )
    assert not _is_empty_consumption_search(
        FakeResponse(200, {"contract_version": "unexpected/v2", "results": []})
    )
    assert not _is_empty_consumption_search(
        FakeResponse(
            200,
            {
                "contract_version": "hardware-knowledge-consumption/v1",
                "results": [{"knowledge_id": "unexpected"}],
            },
        )
    )
