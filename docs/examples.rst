Examples
========

Runnable scripts live in the ``examples/`` directory of the repository. Each
one runs from a clone with ``python examples/<name>.py``; they are exercised
by the test suite (``tests/test_examples.py``).

.. list-table::
   :header-rows: 1
   :widths: 30 50 20

   * - Script
     - What it shows
     - Install
   * - ``ising_tc.py``
     - The canonical campaign: two drivers, identical budgets, locating the
       critical temperature of the 2D Ising model (Onsager's exact value as
       ground truth). Takes about a minute.
     - core
   * - ``chemistry_recommend.py``
     - The chemistry vertical: ranking candidates with ``recommend``.
     - ``cadaques[bo]``
   * - ``pc1d.py``
     - An in-process Oracle of your own (Listing 1 of the SoftwareX article):
       ``price()`` declares the cost, ``evaluate()`` returns the result with
       its measured cost; a budgeted campaign prints declared against settled
       spend and the best point.
     - ``cadaques[bo]``
   * - ``external_reserve_settle.py``
     - Evaluations outside the process: an ``ExternalOracle`` campaign
       operated through the agent ``Toolbox`` (reserve, a simulated external
       job, settle with the measured cost). See
       :doc:`howto_external`.
     - core

``pc1d.py``
-----------

.. literalinclude:: ../examples/pc1d.py
   :language: python
   :lines: 21-

``external_reserve_settle.py``
------------------------------

See :doc:`howto_external` for the annotated walk-through of this script.
