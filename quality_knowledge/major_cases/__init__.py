"""REQ-022 major review knowledge base.

This package deliberately owns a separate SQLite database and attachment root.
The existing business database is only accessed through a read-only gateway.
"""

from .repository import MajorKnowledgeRepository
from .restore import MajorCaseRestoreService
from .service import MajorCaseService
from .sources import BusinessSourceGateway, SqliteBusinessSourceGateway

__all__ = [
    "BusinessSourceGateway",
    "MajorCaseRestoreService",
    "MajorCaseService",
    "MajorKnowledgeRepository",
    "SqliteBusinessSourceGateway",
]
