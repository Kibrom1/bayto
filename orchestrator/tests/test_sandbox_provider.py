"""M2.4: SandboxProvider is an ABC on purpose -- an incomplete implementation must fail
fast at instantiation, not at first use."""
import pytest

from orchestrator.sandbox.provider import SandboxProvider
from orchestrator.sandbox.local import LocalSbxSandboxProvider


def test_incomplete_implementation_cannot_be_instantiated():
    class Incomplete(SandboxProvider):
        async def create(self, task_id, *, name):
            raise NotImplementedError

    with pytest.raises(TypeError):
        Incomplete()


def test_local_provider_implements_every_abstract_method():
    assert LocalSbxSandboxProvider(sessionmaker=None)
