from .analytic import AnalyticOracle, quadratic_bowl
from .dataset import DatasetOracle
from .ising import Ising2DOracle, T_C_EXACT

__all__ = ["AnalyticOracle", "DatasetOracle", "Ising2DOracle", "T_C_EXACT", "quadratic_bowl"]
