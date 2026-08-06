"""Compatibility shim (0.1 layout). Canonical homes since 0.2:

* :class:`Query`, :class:`Result` -> ``cadaques.core.records``
* :class:`Oracle` -> ``cadaques.protocols.oracle``
* :class:`Driver` -> ``cadaques.protocols.driver``
"""

from ..protocols.driver import Driver
from ..protocols.oracle import Oracle
from .records import Query, Result

__all__ = ["Driver", "Oracle", "Query", "Result"]
