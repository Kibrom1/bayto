"""SandboxProvider seam (M2.4): create/start/wake/stop/remove/reconcile a team sandbox.

See `provider.py` for the abstract interface and `local.py` for the local `sbx` CLI
implementation used today; a cloud provider can implement the same ABC later.
"""
from .local import LocalSbxSandboxProvider, SubprocessSbxRunner
from .provider import (
    SandboxCommandError,
    SandboxDrift,
    SandboxInfo,
    SandboxProvider,
    SandboxProviderError,
)

__all__ = [
    "SandboxProvider",
    "SandboxProviderError",
    "SandboxCommandError",
    "SandboxInfo",
    "SandboxDrift",
    "LocalSbxSandboxProvider",
    "SubprocessSbxRunner",
]
