"""A3 test bootstrap: production has no YAML fallback; tests explicitly bootstrap DB mappings."""
import pytest
from pathlib import Path

def _bootstrap(db_path):
    from quality_knowledge.mapping.repository import MappingConfigurationRepository
    from quality_knowledge.mapping.migration import LegacyYamlMappingMigrator
    mr=MappingConfigurationRepository(db_path);mig=LegacyYamlMappingMigrator(mr);cfg=Path(__file__).resolve().parents[1]/'quality_knowledge'/'config'
    for bt in ('HMI','PLC','IFA'):
        if not mr.get_effective_config(bt):mig.migrate(cfg/f'{bt.lower()}_fields.yaml',bt,dry_run=False,created_by='pytest-a3-bootstrap')

@pytest.fixture(autouse=True)
def _a3_mapping_bootstrap(monkeypatch):
    from quality_knowledge.repositories import IssueKnowledgeRepository, SqliteIssueKnowledgeRepository
    for cls in (IssueKnowledgeRepository,SqliteIssueKnowledgeRepository):
        original=cls.__init__
        def make_init(orig):
            def init(self,db_path,*a,**kw):orig(self,db_path,*a,**kw);_bootstrap(self.db_path)
            return init
        monkeypatch.setattr(cls,'__init__',make_init(original))
