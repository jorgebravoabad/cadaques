"""Reference drivers. BayesianDriver requires the [bo] extra and is
imported lazily so core installs stay light (ADR-0008)."""

from .reference import AnnealedLocalDriver, RandomDriver

__all__ = ["AnnealedLocalDriver", "BayesianDriver", "RandomDriver"]


def __getattr__(name: str):
    if name == "BayesianDriver":
        from .bo import BayesianDriver

        return BayesianDriver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
