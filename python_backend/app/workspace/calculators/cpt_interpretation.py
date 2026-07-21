"""Deterministic CPT interpretation (Robertson SBTn).

Turns a raw :class:`~app.workspace.parsers.cpt.CptSounding` into per-depth
engineering quantities using the normalized Soil Behaviour Type (SBTn)
framework of Robertson (1990, 2009) with the unit-weight and area-correction
relations of Robertson & Cabal (2010).

EVERY number a human (or the AI Interpretation) ever sees originates here, in
plain deterministic Python. Nothing in this module calls an LLM. The pipeline
per depth is:

    qt   = qc + u2 * (1 - a)                      (area-corrected cone resistance)
    Rf   = fs / qt * 100                          (friction ratio, %)
    gamma= f(Rf, qt)                              (Robertson & Cabal 2010)
    sigma_v0  = integral of gamma dz              (total vertical stress)
    u0        = gamma_w * (z - gwl)               (hydrostatic pore pressure)
    sigma_v0' = sigma_v0 - u0                     (effective vertical stress)
    Qtn  = ((qt - sigma_v0)/pa) * (pa/sigma_v0')^n  with n iterated from Ic
    Fr   = fs / (qt - sigma_v0) * 100             (normalized friction ratio, %)
    Ic   = sqrt((3.47 - log10 Qtn)^2 + (log10 Fr + 1.22)^2)
    Bq   = (u2 - u0) / (qt - sigma_v0)            (pore-pressure ratio)

The SBT zone/name is then read off Ic (Robertson 2010 boundaries).

Units in: z [m], qc [MPa], fs [kPa], u2 [kPa]. Stresses are computed in kPa;
qc/qt are reported in MPa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from app.workspace.parsers.cpt import CptSounding

# --- Physical constants -----------------------------------------------------
GAMMA_W = 9.81           # unit weight of water [kN/m^3]
PA_KPA = 100.0           # atmospheric reference pressure [kPa]
PA_MPA = 0.1             # atmospheric reference pressure [MPa]

# Effective/total stress is floored to this near the surface so the very first
# (near-zero depth) reading does not divide by zero when normalizing.
MIN_STRESS_KPA = 1.0
# Net cone/friction denominators are floored so Fr/Bq stay finite in soft soils
# where qt can approach sigma_v0.
MIN_NET_KPA = 1.0
# Rf/Fr are floored before log10 in the gamma and Ic relations.
MIN_RF_PCT = 0.1


@dataclass(frozen=True)
class DepthResult:
    """Derived CPT quantities at a single depth. All fields deterministic."""

    z: float            # depth [m]
    qc: float           # measured cone resistance [MPa]
    qt: float           # area-corrected total cone resistance [MPa]
    fs: float           # sleeve friction [kPa]
    u2: float           # measured pore pressure behind cone [kPa]
    u0: float           # hydrostatic pore pressure [kPa]
    sigma_v0: float     # total vertical stress [kPa]
    sigma_v0_eff: float # effective vertical stress [kPa]
    Rf: float           # friction ratio fs/qt [%]
    Fr: float           # normalized friction ratio [%]
    Qtn: float          # normalized cone resistance [-]
    Bq: float           # pore-pressure ratio [-]
    Ic: float           # SBTn index [-]
    sbt_zone: int       # Robertson SBTn zone number (2..7)
    sbt_name: str       # human-readable soil behaviour type


@dataclass
class CptInterpretationResult:
    """Full deterministic interpretation of a sounding + its provenance."""

    rows: List[DepthResult] = field(default_factory=list)
    max_depth: float = 0.0
    gwl: float = 0.0
    area_ratio: float = 0.80
    area_ratio_source: str = "default"

    def __len__(self) -> int:
        return len(self.rows)


# Robertson (2010) SBTn zones keyed off Ic. Zones 1, 8 and 9 require the full
# Qtn-Fr chart position (not Ic alone), so the Ic-based classifier covers the
# common zones 2-7 that dominate ordinary soundings.
#
# Each tuple: (exclusive upper Ic bound, zone number, name). Evaluated top-down;
# the first bound Ic is strictly less than wins.
_SBT_BY_IC = (
    (1.31, 7, "Gravelly sand to dense sand"),
    (2.05, 6, "Sand: clean sand to silty sand"),
    (2.60, 5, "Sand mixtures: silty sand to sandy silt"),
    (2.95, 4, "Silt mixtures: clayey silt to silty clay"),
    (3.60, 3, "Clays: clay to silty clay"),
    (math.inf, 2, "Organic soils: peat / clay"),
)


def _sbt_from_ic(ic: float) -> tuple[int, str]:
    for upper, zone, name in _SBT_BY_IC:
        if ic < upper:
            return zone, name
    # Unreachable (last bound is inf) but keep the type-checker happy.
    return 2, "Organic soils: peat / clay"


def _unit_weight(rf_pct: float, qt_mpa: float) -> float:
    """Estimate soil unit weight [kN/m^3] (Robertson & Cabal 2010).

        gamma/gamma_w = 0.27*log10(Rf) + 0.36*log10(qt/pa) + 1.236

    Non-circular: depends only on Rf and qt, both known before any stress
    integration. Rf and qt are floored so the logs stay finite.
    """
    rf = max(rf_pct, MIN_RF_PCT)
    qt = max(qt_mpa, 1e-3)
    ratio = 0.27 * math.log10(rf) + 0.36 * math.log10(qt / PA_MPA) + 1.236
    gamma = ratio * GAMMA_W
    # Keep within physically sane bounds (very soft organic .. dense gravel).
    return min(max(gamma, 12.0), 24.0)


def _ic_from_normalized(qtn: float, fr_pct: float) -> float:
    """Robertson SBTn index Ic from Qtn and Fr."""
    qtn = max(qtn, 1e-6)
    fr = max(fr_pct, MIN_RF_PCT)
    return math.sqrt(
        (3.47 - math.log10(qtn)) ** 2 + (math.log10(fr) + 1.22) ** 2
    )


def _solve_qtn_ic(qt_kpa: float, fs_kpa: float, sigma_v0: float,
                  sigma_v0_eff: float) -> tuple[float, float, float]:
    """Iterate the stress-normalization exponent n to convergence.

    Returns (Qtn, Fr, Ic). n depends on Ic which depends on Qtn which depends
    on n, so we fixed-point iterate (Robertson 2009). Converges in a handful of
    steps; capped at 20 for safety.
    """
    net_q = max(qt_kpa - sigma_v0, MIN_NET_KPA)
    fr = fs_kpa / net_q * 100.0
    ic = 2.0  # neutral starting guess (silty)
    qtn = net_q / PA_KPA
    for _ in range(20):
        n = min(1.0, 0.381 * ic + 0.05 * (sigma_v0_eff / PA_KPA) - 0.15)
        n = max(n, 0.0)
        qtn = (net_q / PA_KPA) * (PA_KPA / sigma_v0_eff) ** n
        new_ic = _ic_from_normalized(qtn, fr)
        if abs(new_ic - ic) < 1e-6:
            ic = new_ic
            break
        ic = new_ic
    return qtn, fr, ic


def interpret_cpt(
    sounding: CptSounding,
    *,
    gwl: Optional[float] = None,
    unit_weight: Optional[float] = None,
) -> CptInterpretationResult:
    """Run the full deterministic interpretation over a parsed sounding.

    Optional engineer overrides (default = derive from the sounding):
      * ``gwl`` — groundwater level (m); overrides the file header value.
      * ``unit_weight`` — a single soil unit weight (kN/m^3) applied to the whole
        profile instead of the per-depth Robertson & Cabal (2010) estimate.
    """
    gwl = sounding.gwl if gwl is None else gwl
    a = sounding.area_ratio

    results: List[DepthResult] = []
    sigma_v0 = 0.0
    prev_z = 0.0

    for row in sounding.rows:
        z = row.z
        # Area-corrected cone resistance. u2 is kPa, qc/qt are MPa.
        qt_mpa = row.qc + (row.u2 / 1000.0) * (1.0 - a)
        qt_kpa = qt_mpa * 1000.0

        # Friction ratio (uses total corrected resistance).
        rf_pct = row.fs / qt_kpa * 100.0 if qt_kpa > 0 else 0.0

        # Integrate total vertical stress with the layer-appropriate unit
        # weight. The slab from prev_z..z uses this row's gamma estimate, unless
        # the engineer supplied a single unit weight for the whole profile.
        gamma = unit_weight if unit_weight is not None else _unit_weight(rf_pct, qt_mpa)
        dz = max(z - prev_z, 0.0)
        sigma_v0 += gamma * dz
        prev_z = z

        # Hydrostatic pore pressure and effective stress.
        u0 = GAMMA_W * (z - gwl) if z > gwl else 0.0
        sigma_v0_eff = max(sigma_v0 - u0, MIN_STRESS_KPA)
        sigma_v0_floored = max(sigma_v0, MIN_STRESS_KPA)

        qtn, fr_pct, ic = _solve_qtn_ic(
            qt_kpa, row.fs, sigma_v0_floored, sigma_v0_eff
        )

        net_q = max(qt_kpa - sigma_v0_floored, MIN_NET_KPA)
        bq = (row.u2 - u0) / net_q

        zone, name = _sbt_from_ic(ic)

        results.append(
            DepthResult(
                z=z,
                qc=row.qc,
                qt=qt_mpa,
                fs=row.fs,
                u2=row.u2,
                u0=u0,
                sigma_v0=sigma_v0_floored,
                sigma_v0_eff=sigma_v0_eff,
                Rf=rf_pct,
                Fr=fr_pct,
                Qtn=qtn,
                Bq=bq,
                Ic=ic,
                sbt_zone=zone,
                sbt_name=name,
            )
        )

    return CptInterpretationResult(
        rows=results,
        max_depth=sounding.max_depth,
        gwl=gwl,
        area_ratio=a,
        area_ratio_source=sounding.area_ratio_source,
    )
