from .analytic import AnalyticOracle, quadratic_bowl
from .dataset import DatasetOracle
from .external import ExternalEvaluation, ExternalOracle
from .ising import Ising2DOracle, T_C_EXACT

__all__ = [
    "AnalyticOracle", "DatasetOracle", "ExternalEvaluation", "ExternalOracle",
    "Ising2DOracle", "T_C_EXACT", "quadratic_bowl",
]
