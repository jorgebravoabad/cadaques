"""Featurize the OCM-291 catalyst table into a continuous descriptor space.

The Nguyen/Taniike 291-catalyst summary encodes each catalyst as three
elements (M1, M2, M3) on a support — categorical identities the current
kernel does not parametrize. This module maps them to continuous
elemental descriptors (Pauling electronegativity, Shannon ionic radius
of the common cation, atomic number), slot-wise, plus a one-hot support
encoding and the measured reaction conditions — turning the table into
a fully continuous :class:`~cadaques.verticals.chemistry.ReactionTable`.

This is study-level code on purpose: featurization choices are part of
the *study's* frozen protocol, not of the package. Element properties
are hardcoded for exactly the 27 elements present (plus 'none'), so the
study has zero extra dependencies and the descriptor table is itself
versioned with the protocol.

Descriptor references: Pauling electronegativities (standard tabulation);
Shannon effective ionic radii (Å, six-coordinate, common oxidation state
in oxide catalysts). 'none' (empty slot) maps to zeros with loading 0.
"""

from __future__ import annotations

import csv
from pathlib import Path

from cadaques.verticals import ReactionTable

# element -> (electronegativity_pauling, ionic_radius_A, atomic_number)
ELEMENT_DESCRIPTORS: dict[str, tuple[float, float, int]] = {
    "Li": (0.98, 0.76, 3),
    "Na": (0.93, 1.02, 11),
    "K": (0.82, 1.38, 19),
    "Cs": (0.79, 1.67, 55),
    "Mg": (1.31, 0.72, 12),
    "Ca": (1.00, 1.00, 20),
    "Sr": (0.95, 1.18, 38),
    "Ba": (0.89, 1.35, 56),
    "Y": (1.22, 0.90, 39),
    "La": (1.10, 1.03, 57),
    "Ce": (1.12, 0.87, 58),
    "Nd": (1.14, 0.98, 60),
    "Eu": (1.20, 0.95, 63),
    "Tb": (1.10, 0.92, 65),
    "Ti": (1.54, 0.61, 22),
    "Zr": (1.33, 0.72, 40),
    "Hf": (1.30, 0.71, 72),
    "V": (1.63, 0.54, 23),
    "Mo": (2.16, 0.65, 42),
    "W": (2.36, 0.66, 74),
    "Mn": (1.55, 0.83, 25),
    "Fe": (1.83, 0.65, 26),
    "Co": (1.88, 0.65, 27),
    "Ni": (1.91, 0.69, 28),
    "Pd": (2.20, 0.86, 46),
    "Cu": (1.90, 0.73, 29),
    "Zn": (1.65, 0.74, 30),
    "none": (0.0, 0.0, 0),
}

SUPPORTS = ("Al2O3", "BaO", "CaO", "CeO2", "La2O3", "MgO", "SiO2", "TiO2", "ZrO2")

CONDITIONS = {
    "Temp. (℃)": "T_celsius",
    "CH4/O2 (mol/mol)": "ch4_o2_ratio",
    "Total flow (mL/min)": "total_flow",
    "PAr (atm)": "p_ar",
}


def featurize_row(row: dict) -> dict:
    out: dict[str, float] = {}
    for slot in ("M1", "M2", "M3"):
        element = row[slot].strip()
        if element not in ELEMENT_DESCRIPTORS:
            raise KeyError(f"Element {element!r} missing from the descriptor table")
        en, radius, z = ELEMENT_DESCRIPTORS[element]
        out[f"{slot}_en"] = en
        out[f"{slot}_radius"] = radius
        out[f"{slot}_Z"] = float(z)
    support = row["Support"].strip()
    for s in SUPPORTS:
        out[f"support_{s}"] = 1.0 if support == s else 0.0
    for raw, clean in CONDITIONS.items():
        out[clean] = float(row[raw])
    out["c2_yield"] = float(row["C2 yield (%)"])
    return out


def load_ocm291(path: str | Path) -> ReactionTable:
    """The featurized OCM-291 study table.

    Composition descriptors: slot-wise (EN, ionic radius, Z) for
    M1/M2/M3 plus one-hot support. Conditions: T, CH4/O2, total flow,
    p(Ar) as measured at each catalyst's best-performance point.
    Objective: C2 yield (%), maximize. Cost: uniform tariff (the HTE
    campaign screened all points at comparable per-point effort; this
    dataset therefore exercises driver comparison per query, while the
    Olympus flow datasets exercise heterogeneous real costs).
    """
    rows = [featurize_row(r) for r in csv.DictReader(open(path, encoding="utf-8"))]
    composition = tuple(
        f"{slot}_{d}" for slot in ("M1", "M2", "M3") for d in ("en", "radius", "Z")
    ) + tuple(f"support_{s}" for s in SUPPORTS)
    return ReactionTable(
        rows=rows,
        objective="c2_yield",
        composition=composition,
        conditions=tuple(CONDITIONS.values()),
        direction="maximize",
        tariff={"seconds": 1.0},  # abstract per-experiment unit (uniform HTE effort)
        tolerance=float("inf"),  # nearest-serve benchmark mode (22-D)
        name="ocm291_nguyen2020",
    )
