"""Size budgets for the trusted-local workflow. Fixed worker defaults stay narrow."""
from contextlib import contextmanager
from contextvars import ContextVar
import sys

from .identity import require

MIB = 1024 ** 2
DEFAULT_SOURCE = 8 * MIB
DEFAULT_ARTIFACT = 8 * MIB
UNLIMITED = sys.maxsize - 1
DEFAULT_SCRATCH = 512 * MIB
_budget = ContextVar("dreamm_trusted_local_budget", default=None)


def validated_budget(source, artifact):
    require(type(source) is int and type(artifact) is int
            and 0 < source <= UNLIMITED and 0 < artifact <= UNLIMITED,
            "RESOURCE_LIMIT", "source and artifact budgets must be positive")
    return source, artifact


@contextmanager
def local_budget(source, artifact, scratch=DEFAULT_SCRATCH):
    validated_budget(source, artifact)
    require(type(scratch) is int and 0 < scratch <= UNLIMITED,
            "RESOURCE_LIMIT", "scratch budget must be positive")
    token = _budget.set((source, artifact, scratch))
    try:
        yield
    finally:
        _budget.reset(token)


def source_bytes():
    return (_budget.get() or (DEFAULT_SOURCE, DEFAULT_ARTIFACT))[0]


def artifact_bytes():
    return (_budget.get() or (DEFAULT_SOURCE, DEFAULT_ARTIFACT))[1]


def scratch_bytes():
    value = _budget.get()
    return value[2] if value else DEFAULT_SCRATCH


def artifact_count():
    return max(1024, scratch_bytes() // (512 * 1024))


def trusted_local():
    return _budget.get() is not None


def model_count():
    return 9999 if trusted_local() else 20  # fixed-width PDB MODEL field


def source_atom_count():
    # The aggregate source may contain many models. The PDB tools still
    # enforce their own (per-frame) limits.
    return source_bytes() // 64 if trusted_local() else 100000
