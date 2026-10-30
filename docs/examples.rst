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
   * - ``listing_oracle.py``
     - Listing 1 of the SoftwareX article: your own simulation as an
       in-process Oracle (``price()`` declares the cost, ``evaluate()``
       returns the result with its measured cost), run by the shipped
       Gaussian-process driver; prints the stop reason, the spend and the
       planner's share of it.
     - ``cadaques[bo]``
   * - ``pc1d.py``
     - A one-dimensional photonic crystal (a quarter-wave stack) as an
       in-process Oracle; a budgeted campaign prints declared against settled
       spend and the best point.
     - ``cadaques[bo]``
   * - ``external_reserve_settle.py``
     - Evaluations outside the process: an ``ExternalOracle`` campaign
       operated through the agent ``Toolbox`` (reserve, a simulated external
       job, settle with the measured cost). See
       :doc:`howto_external`.
     - core

``listing_oracle.py``
---------------------

.. literalinclude:: ../examples/listing_oracle.py
   :language: python
   :start-after: # ---- quoted in the article from here

``pc1d.py``
-----------

.. literalinclude:: ../examples/pc1d.py
   :language: python
   :lines: 21-

``external_reserve_settle.py``
------------------------------

See :doc:`howto_external` for the annotated walk-through of this script.
