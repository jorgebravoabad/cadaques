"""Power of the exact McNemar test for the primary comparison (Study v2, section 6)."""
import numpy as np
from scipy.stats import binomtest
rng = np.random.default_rng(0)
def power(n, p10, p01, sims=20000, alpha=0.05):
    hits = 0
    for _ in range(sims):
        u = rng.random(n); b = (u < p10).sum(); c = ((u >= p10) & (u < p10+p01)).sum()
        if b + c and binomtest(b, b+c, 0.5, alternative="two-sided").pvalue < alpha: hits += 1
    return hits/sims
# p10: A correct & B wrong; p01: A wrong & B correct
for n in (20, 30, 40, 60):
    print(n, " ".join(f"{p10:.2f}/{p01:.2f}:{power(n,p10,p01):.2f}" for p10,p01 in [(0.15,0.03),(0.20,0.03),(0.30,0.05),(0.40,0.05)]))
