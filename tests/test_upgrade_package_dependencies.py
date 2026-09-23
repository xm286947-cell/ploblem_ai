from scripts.build_upgrade_package import changed_files


def test_upgrade_package_carries_issue_period_runtime_dependency():
    names={path.name for path in changed_files()}
    assert 'v1_repository.py' in names
    assert 'issue_period.py' in names
    assert 'sqlite_tuning.py' in names
