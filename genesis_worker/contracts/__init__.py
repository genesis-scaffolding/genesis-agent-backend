"""The framework/plugin contract — the only `genesis_worker` module a plugin may import.

See ADR-009. Everything a source or service plugin needs lives here: the ABCs it
implements, the types that cross the boundary, and the context it is constructed with.
"""

from .acquire import (
    AcquireChoice,
    AcquireProgress,
    AcquireSession,
    AcquireState,
    AcquireStateKind,
    AcquireView,
)
from .catalog import Catalog, DiscoveredModel, ModelEntry, ModelPiece
from .classify import (
    COMPONENT_DIRS,
    SKIP_FILENAMES,
    WEIGHT_EXTS,
    classify,
    role_sort_key,
)
from .context import PluginContext, ServiceContext, SourceContext
from .host import Hardware, HostInfo
from .install import InstallState, InstallVersion, ServiceInstall
from .plugin import Plugin
from .secret import NoSecretsAccessor, SecretsAccessor, StaticSecretsAccessor
from .service import (
    InferenceService,
    ServiceCapabilities,
    ServiceCategory,
    ServiceResourceEstimate,
    ServiceState,
    ServiceStatus,
    StartResult,
    StopResult,
)
from .source import ModelSource
from .ui import UiPage

__all__ = [
    "COMPONENT_DIRS",
    "SKIP_FILENAMES",
    "WEIGHT_EXTS",
    "AcquireChoice",
    "AcquireProgress",
    "AcquireSession",
    "AcquireState",
    "AcquireStateKind",
    "AcquireView",
    "Catalog",
    "DiscoveredModel",
    "Hardware",
    "HostInfo",
    "InferenceService",
    "InstallState",
    "InstallVersion",
    "ModelEntry",
    "ModelPiece",
    "ModelSource",
    "NoSecretsAccessor",
    "Plugin",
    "PluginContext",
    "SecretsAccessor",
    "ServiceCapabilities",
    "ServiceCategory",
    "ServiceContext",
    "ServiceInstall",
    "ServiceResourceEstimate",
    "ServiceState",
    "ServiceStatus",
    "SourceContext",
    "StartResult",
    "StaticSecretsAccessor",
    "StopResult",
    "UiPage",
    "classify",
    "role_sort_key",
]
