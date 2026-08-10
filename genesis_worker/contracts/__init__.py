"""The framework/plugin contract — the only `genesis_worker` module a plugin may import.

See ADR-009. Everything a source or service plugin needs lives here: the ABCs it
implements, the types that cross the boundary, and the context it is constructed with.
"""

from .catalog import Catalog, DiscoveredModel, ModelEntry, ModelPiece
from .classify import (
    COMPONENT_DIRS,
    SKIP_FILENAMES,
    WEIGHT_EXTS,
    classify,
    role_sort_key,
)
from .service import (
    InferenceService,
    ServiceCapabilities,
    ServiceResourceEstimate,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
)
from .source import (
    AcquireChoice,
    AcquireFileGroup,
    AcquireProgress,
    AcquireSession,
    AcquireState,
    AcquireStep,
    ModelSource,
)

__all__ = [
    "COMPONENT_DIRS",
    "SKIP_FILENAMES",
    "WEIGHT_EXTS",
    "AcquireChoice",
    "AcquireFileGroup",
    "AcquireProgress",
    "AcquireSession",
    "AcquireState",
    "AcquireStep",
    "Catalog",
    "DiscoveredModel",
    "InferenceService",
    "ModelEntry",
    "ModelPiece",
    "ModelSource",
    "ServiceCapabilities",
    "ServiceResourceEstimate",
    "ServiceState",
    "ServiceStatus",
    "StartResult",
    "StopResult",
    "classify",
    "role_sort_key",
]
