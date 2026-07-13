Concepts
========

The dual architecture
---------------------

CADAQUES is agnostic on both sides of the discovery loop. An
:class:`~cadaques.Oracle` is anything that answers a
:class:`~cadaques.Query` at a price; a :class:`~cadaques.Driver` is
anything that decides what to ask next. Both are
:class:`typing.Protocol` classes with two methods each — anything that
speaks the protocol plugs in, with no inheritance required.

Declared vs. settled cost
-------------------------

Oracles declare a price *ex ante* (:meth:`Oracle.price`) so the runner
can check affordability before committing; the actual cost is settled
*ex post* inside each :class:`~cadaques.Result`. Real oracles deviate
from their estimates — the :class:`~cadaques.Ledger` records both, and
the discrepancy is itself an observable.

Both sides are metered
----------------------

Driver decisions cost wall time — and tokens, if the driver is an LLM
agent. A campaign's economics therefore include the price of
intelligence, enabling the question: *when does an expensive smart
driver beat a cheap dumb one?*

Budget-aware strategies
-----------------------

Drivers receive a read-only :class:`~cadaques.BudgetView` and may adapt
their strategy to the remaining budget: the reference
:class:`~cadaques.drivers.AnnealedLocalDriver` explores while rich and
exploits while poor.

Multi-currency budgets
----------------------

A :class:`~cadaques.Cost` is a vector of heterogeneous currencies —
wall time, CPU hours, euros, tokens. A campaign's
:class:`~cadaques.Budget` caps any subset of them; the campaign stops
when the first currency is exhausted.
