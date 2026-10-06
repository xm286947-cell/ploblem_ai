from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_operator_urls_use_port_9000_and_container_uses_port_8080():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["public-knowledge"]
    assert service["ports"] == ["127.0.0.1:9000:8080"]
    assert "127.0.0.1:8080/health" in service["healthcheck"]["test"][-1]
    assert "EXPOSE 8080" in (ROOT / "Dockerfile").read_text(encoding="utf-8")

    health = (ROOT / "scripts/health.sh").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/e2e_smoke.py").read_text(encoding="utf-8")
    starter = (ROOT / "start.sh").read_text(encoding="utf-8")
    admin = (ROOT / "docs/PUBLIC_KNOWLEDGE_RAG_ADMIN_CONFIG.md").read_text(encoding="utf-8")
    infra = (ROOT / "docs/PUBLIC_KNOWLEDGE_RAG_INFRA_001.md").read_text(encoding="utf-8")

    assert 'PKR_BASE_URL:-http://127.0.0.1:9000' in health
    assert '"http://127.0.0.1:9000"' in smoke
    assert "http://127.0.0.1:9000" in starter
    assert "http://127.0.0.1:9000/settings" in admin
    assert "http://127.0.0.1:9000/health" in infra
    assert "127.0.0.1:9000:8080" in admin and "container" in admin.lower()


def test_operator_documentation_does_not_present_8080_as_host_url():
    for relative in (
        "docs/PUBLIC_KNOWLEDGE_RAG_ADMIN_CONFIG.md",
        "docs/PUBLIC_KNOWLEDGE_RAG_INFRA_001.md",
    ):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "http://127.0.0.1:8080" not in content
