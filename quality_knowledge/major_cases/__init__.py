"""Minimal Major Knowledge domain package for case publication.

The mainline promotion intentionally exposes only the independent repository.
Additional Major services remain outside this promotion boundary.
"""

from .repository import MajorKnowledgeRepository

__all__ = ["MajorKnowledgeRepository"]
