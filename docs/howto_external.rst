Running evaluations outside the process
=======================================

Many evaluations cannot run inside the Python process that owns the
campaign: a DFT job in a batch queue, a measurement on an instrument. The
kernel still has to price them before they run and charge what they
actually cost afterwards. This page shows the cycle that does that:
**reserve, run, settle**.

ExternalOracle: a price model, nothing else
-------------------------------------------

:class:`~cadaques.oracles.external.ExternalOracle` declares the cost of an
evaluation; it never runs one. The declared price is a tariff, optionally
scaled by a power law in the fidelity knobs. With
``tariff={"seconds": 30.0}``, ``scaling={"resolution": 2.0}`` and
``reference={"resolution": 16}``, a ``resolution=32`` query is priced at
120 s. The parameters are plain JSON, so the oracle travels inside a
campaign spec, which is what the agent tools take.

The declared cost is held against the budget when you *reserve* an
evaluation, and it is replaced
by the measured cost when you *settle* it.

The cycle through Python tools
------------------------------

The complete script below creates a campaign in a temporary workspace,
reserves four evaluations, runs a simulated external job for each whose
measured cost differs from the declaration, settles it, and prints the
ledger. It needs only the core install.

.. literalinclude:: ../examples/external_reserve_settle.py
   :language: python
   :caption: examples/external_reserve_settle.py
   :lines: 24-

The tools used, in order:

``create_campaign``
   Builds the campaign from a spec (oracle, driver, budget, task) in the
   workspace directory.
``reserve_evaluation``
   Asks the driver for the next point, checks that its declared cost fits,
   and holds it. The result carries the ``evaluation_id``, the parameters to
   run, and the declared cost.
``settle_evaluation``
   Closes a reservation with an ``outcome`` (``completed`` or ``failed``),
   the ``value`` for a completed one, and the measured ``settled_cost``.
   A reservation that will never run is closed with ``cancel_evaluation``.
``get_campaign_status``
   Budget (total, spent, reserved, available), pending reservations and the
   best result so far.

Every mutating call carries an ``actor`` (who is acting) and a
``request_id`` (see below); both are recorded in the event log.

The same cycle from the command line
------------------------------------

``cadaques-agent call`` exposes the same tools to shell scripts and job
wrappers. The workspace is the ``--root`` directory (or the
``CADAQUES_WORKSPACE`` environment variable); arguments are one JSON object,
or ``-`` to read it from standard input, and the response envelope is printed
as JSON:

.. code-block:: bash

   export CADAQUES_WORKSPACE=./ws
   ACTOR='{"kind": "human", "id": "alice"}'

   cadaques-agent call create_campaign "{\"campaign_id\": \"demo\", \"actor\": $ACTOR,
     \"request_id\": \"create\", \"spec\": $(cat spec.json)}"

   cadaques-agent call reserve_evaluation \
     "{\"campaign_id\": \"demo\", \"actor\": $ACTOR, \"request_id\": \"reserve-1\"}"
   # -> result.evaluation_id and result.params: submit the job with these

   cadaques-agent call settle_evaluation "{\"campaign_id\": \"demo\", \"actor\": $ACTOR,
     \"request_id\": \"settle-1\", \"evaluation_id\": \"ev-000002\",
     \"outcome\": \"completed\", \"value\": 0.59, \"settled_cost\": {\"seconds\": 25.2}}"

   cadaques-agent call get_campaign_status '{"campaign_id": "demo"}'

Each call is a separate process: the campaign is rebuilt from the workspace
on disk every time, so different people, scripts or machines can take turns
operating one campaign.

Settling from scheduler accounting
----------------------------------

The cost to settle is what the job really consumed, as measured by whoever
ran it, not what was declared. On Slurm that is the scheduler's accounting.
Once accounting for the job is final, read the CPU time in seconds:

.. code-block:: bash

   sacct -j <jobid> -X -n -o CPUTimeRAW

``CPUTimeRAW`` is allocated CPU time in seconds (elapsed time multiplied by
allocated CPUs), which is the quantity to pass as ``settled_cost`` when the
budget is in ``seconds``. Wait until the job has reached a final state and
``sacct`` reports it; settling from a still-running job's partial
accounting would enter an unfinished number into the ledger. A job that
failed after consuming resources is settled with ``outcome="failed"``, a
``failure`` record, and the cost it did consume.

Always pass ``settled_cost``
----------------------------

Omitting ``settled_cost`` still works in this release: the declared cost is
charged and the event records ``settled_source: "declared_default"``. That
is deprecated since 0.4.1 (removal in 0.6.0) and now emits a
``DeprecationWarning`` and a ``warnings`` entry in the response. The reason
is accounting honesty: the declaration is a forecast, and entering a forecast
as if it were a measurement makes the ledger look settled when nothing was
measured. Pass the measured cost every time; if you really have no
measurement, say so explicitly rather than relying on the default.

Idempotent request ids, and retrying after a crash
--------------------------------------------------

Every mutating call is keyed by its ``request_id``. Sending the same request
id again does not act twice: the recorded answer is returned, marked
``"replayed": true``. Two consequences make crash recovery simple:

* **Retry freely.** If a wrapper script dies, or a network call times out,
  after sending ``settle_evaluation`` with ``request_id="settle-1"``, run the
  identical call again. If the first one had been recorded, you get the same
  response and nothing is charged twice; if not, it is applied now.
* **Derive request ids from the work, not from the clock.** Use something
  stable such as ``f"settle-{slurm_job_id}"`` or ``f"reserve-{i}"``, so that
  a restarted script reproduces the same ids. A fresh random id on every
  attempt defeats the protection.

After a crash between ``reserve_evaluation`` and ``settle_evaluation``, the
reservation is still held. ``get_campaign_status`` lists the pending
reservations; settle each one once its job's accounting is final, or cancel
it with ``cancel_evaluation`` if the job was never submitted.
