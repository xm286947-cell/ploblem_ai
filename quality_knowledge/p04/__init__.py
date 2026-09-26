"""P04 QualityScenario insight domain.

The package is deliberately provider-neutral.  P04 consumes a small public
port and never reaches into another domain's database or repository.
"""

from .contracts import P04State, P04View
from .fixtures import FixtureP04Provider
from .service import P04InsightService

__all__ = ["FixtureP04Provider", "P04InsightService", "P04State", "P04View"]
