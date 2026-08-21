CADAQUES
========

**Cost-Aware Dual Architecture for QUery-Efficient diScovery**

*An open-source framework for autonomous discovery campaigns:
any oracle, any driver, one budget. Every query counts.*

CADAQUES decouples autonomous discovery into two symmetric protocols.
A metered :class:`~cadaques.Oracle` abstracts anything that answers
queries at a price — a simulator, a laboratory instrument, an analytic
function. A :class:`~cadaques.Driver` abstracts anything that decides
what to ask next — random search, Bayesian optimization, gradient
methods, LLM agents. Between them sits the framework's one structural
commitment: **cost is a first-class primitive**. Every oracle query and
every driver decision is priced in heterogeneous currencies and charged
against a single campaign :class:`~cadaques.Budget`.

Installation
------------

.. code-block:: bash

   pip install cadaques          # core: NumPy only
   pip install "cadaques[bo]"    # + the Gaussian-process driver

.. toctree::
   :maxdepth: 2
   :caption: Guide

   quickstart
   concepts

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api/core
   api/oracles
   api/drivers
   api/runtime
   api/stats
   api/verticals
   api/testing

Citation
--------

If you use CADAQUES in academic work, please cite it:
`doi:10.5281/zenodo.21293589 <https://doi.org/10.5281/zenodo.21293589>`_
(concept DOI; per-release DOIs are listed in the
`CHANGELOG <https://github.com/jorgebravoabad/cadaques/blob/main/CHANGELOG.md>`_)
and the paper: `arXiv:2607.16127 <https://arxiv.org/abs/2607.16127>`_.
