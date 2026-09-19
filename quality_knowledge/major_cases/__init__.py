"""REQ-022 major review knowledge base.

This package deliberately owns a separate SQLite database and attachment root.
The existing business database is only accessed through a read-only gateway.
"""

from .repository import MajorKnowledgeRepository
from .service import MajorCaseService
from .sources import BusinessSourceGateway, SqliteBusinessSourceGateway

__all__ = [
    "BusinessSourceGateway",
    "MajorCaseService",
    "MajorKnowledgeRepository",
    "SqliteBusinessSourceGateway",
]
