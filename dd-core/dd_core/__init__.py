"""dd-core — Dynamic Data: a claim store for AI project memory.

Every fact is a claim carrying source, confidence, time, evidence, and
relationships. Facts accumulate; truth is computed on read.

    from dd_core import DynamicDataStore, Profile

    ddb = DynamicDataStore("project.ddb")
    ddb.assert_claim("exampleapp", "test_pool", "NullPool",
                     source="ezra", confidence=1.0,
                     evidence="conftest.py sets EXAMPLEAPP_ENGINE_NULLPOOL=1")
    print(ddb.resolve("exampleapp", "test_pool").chosen.value)   # -> "NullPool"
"""

from .models import (
    Claim,
    CredenceType,
    DeterminedConflictError,
    Profile,
    Resolution,
    SAME_AS,
)
from .store import DynamicDataStore
from . import signing

__all__ = [
    "DynamicDataStore",
    "Claim",
    "Profile",
    "CredenceType",
    "Resolution",
    "DeterminedConflictError",
    "SAME_AS",
    "signing",
]

__version__ = "0.3.0"
