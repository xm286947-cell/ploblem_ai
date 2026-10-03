"""Versioned migrations for the durable Hardware Asset database."""

from services.hardware_asset_migrations import v001_candidate_repository

__all__ = ["v001_candidate_repository"]
