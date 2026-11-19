"""Size budgets for the trusted-local workflow. Fixed worker defaults stay narrow."""
from contextlib import contextmanager
from contextvars import ContextVar

from .identity import require

MIB = 1024 ** 2
DEFAULT_SOURCE = 8 * MIB
DEFAULT_ARTIFACT = 8 * MIB
LOCAL_SOURCE = 64 * MIB
LOCAL_ARTIFACT = 128 * MIB
DEFAULT_SCRATCH = 512 * MIB
_budget = ContextVar("dreamm_trusted_local_budget", default=None)


def validated_budget(source, artifact):
    require(type(source) is int and type(artifact) is int
            and DEFAULT_SOURCE <= source <= artifact <= 256 * MIB,
            "RESOURCE_LIMIT", "trusted-local source/artifact budget must be between 8 and 256 MiB")
    return source, artifact


@contextmanager
def local_budget(source, artifact, scratch=DEFAULT_SCRATCH):
    validated_budget(source, artifact)
    require(type(scratch) is int and max(artifact, DEFAULT_SCRATCH) <= scratch <= 16384 * MIB,
            "RESOURCE_LIMIT", "trusted-local scratch budget must be between 512 and 16384 MiB")
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
    return 9999 if trusted_local() else 20


def source_atom_count():
    # The aggregate source may contain many models. The PDB tools still
    # enforce their own (per-frame) limits.
    return source_bytes() // 64 if trusted_local() else 100000
