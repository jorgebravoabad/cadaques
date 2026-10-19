"""Verify the 1D reference answer: quarter-wave argmax and gap formula (transfer matrix)."""
import numpy as np
from scipy.optimize import brentq
def rhs(w, f, nH, nL):            # w = omega*a/(2*pi*c); f = d_H/a
    pH, pL = 2*np.pi*w*nH*f, 2*np.pi*w*nL*(1-f)
    return np.cos(pH)*np.cos(pL) - 0.5*(nH/nL+nL/nH)*np.sin(pH)*np.sin(pL)
def gap(f, nH, nL):
    w = np.linspace(1e-4, 1.0/(nH*f+nL*(1-f)), 20001)   # up to 2nd Bragg
    inside = np.abs(rhs(w, f, nH, nL)) > 1
    idx = np.flatnonzero(inside)
    if not len(idx): return 0.0
    run = idx[: np.argmax(np.diff(idx) > 1)+1] if np.any(np.diff(idx) > 1) else idx
    g = lambda x: abs(rhs(x, f, nH, nL)) - 1
    lo = brentq(g, w[run[0]-1], w[run[0]]); hi = brentq(g, w[run[-1]], w[run[-1]+1])
    return 2*(hi-lo)/(hi+lo)
for eps in [(13,1),(12,2),(4,1)]:
    nH, nL = np.sqrt(eps[0]), np.sqrt(eps[1])
    fs = np.linspace(0.02, 0.98, 961); r = np.array([gap(f,nH,nL) for f in fs])
    from scipy.optimize import minimize_scalar
    opt = minimize_scalar(lambda f: -gap(f,nH,nL), bounds=(fs[r.argmax()]-0.002, fs[r.argmax()]+0.002), method="bounded")
    fqw = nL/(nH+nL); exact = 4/np.pi*np.arcsin((nH-nL)/(nH+nL))
    print(f"eps={eps}: argmax d_H/a={opt.x:.5f} (quarter-wave {fqw:.5f}); max ratio={-opt.fun:.5f} (formula {exact:.5f}); ratio at qw={gap(fqw,nH,nL):.5f}")
