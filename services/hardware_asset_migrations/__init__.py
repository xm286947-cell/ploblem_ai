"""Versioned migrations for the durable Hardware Asset database."""

from services.hardware_asset_migrations import v001_candidate_repository
from services.hardware_asset_migrations import v002_legacy_migration

__all__ = ["v001_candidate_repository", "v002_legacy_migration"]
