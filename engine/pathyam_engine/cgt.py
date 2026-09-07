"""Illustrative postprandial glucose curve.

READ THIS BEFORE QUOTING ANY NUMBER THIS PRODUCES
--------------------------------------------------
This is **not a validated clinical model**. It has never been fitted to or tested
against real continuous glucose data, and no golden dataset exists to do so. It
exists to give the app a plausible shape to draw, and it should be labelled as an
illustration everywhere it is shown.

The reason for spelling that out at this length: the previous version presented the
same arithmetic under the heading "Physiological Model", and the UI rendered its
output as "Peak: 168 mg/dL" with no qualification. A person managing type 2 diabetes
reading that number has no way to know it came from three constants nobody sourced.

WHAT IS SUPPORTED, AND WHAT IS NOT
----------------------------------
Supported — glycemic load.
    GL = available carbohydrate (g) x GI / 100
    This is the standard definition (Salmerón et al., 1997), and both inputs come
    from the caller: GI is supplied, not guessed, and carbohydrate comes from the
    engine's IFCT-backed computation.

Supported in direction only — the fat and fibre terms.
    Dietary fat delays gastric emptying, pushing the peak later and lower. Viscous
    soluble fibre attenuates the postprandial rise. Both effects are well established
    in direction and neither is controversial. The *magnitude* here is not taken from
    any published model.

NOT supported — every coefficient below.
    PEAK_MG_DL_PER_GL_UNIT, FAT_DAMPING, FIBRE_DAMPING and the time-to-peak slope
    were chosen to make the curve look reasonable. They are tuning constants, not
    measurements, and they are named so that nobody mistakes them for parameters.

WHAT WOULD MAKE THIS REAL
-------------------------
Paired CGM traces and weighed meal records from the target population, then fitting
these coefficients and reporting curve RMSE against held-out traces. Until that
exists, `is_validated` stays False and every caller must surface the disclaimer.
Citations for the fat and fibre effects should be added through
`evidence.citation_validator`, which verifies that a PMID resolves to the work being
cited -- do not paste identifiers in by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CGTCurve",
    "CurvePoint",
    "MODEL_ID",
    "DISCLAIMER",
    "predict_curve",
]

MODEL_ID = "illustrative-v1"

DISCLAIMER = (
    "Illustrative only — not a validated clinical model. The curve's shape is based "
    "on glycemic load with unsourced adjustments for fat and fibre, and has never "
    "been compared against real glucose readings. Do not use it to make treatment "
    "or dosing decisions."
)

# --- tuning constants. Not measurements. See the module docstring. ----------
PEAK_MG_DL_PER_GL_UNIT = 1.8      # mg/dL of peak rise per unit of glycemic load
FAT_DAMPING = 0.02                # per gram of fat, in exp(-k*g)
FIBRE_DAMPING = 0.04              # per gram of fibre, in exp(-k*g)
BASE_TIME_TO_PEAK_MIN = 45        # mixed meals typically peak in 30-60 min
FAT_DELAY_MIN_PER_G = 1.5         # fat pushes the peak later
MAX_FAT_DELAY_MIN = 45
MIN_PEAK_RISE_MG_DL = 5.0
_CURVE_MINUTES = 180
_CURVE_STEP_MIN = 5


@dataclass(frozen=True)
class CurvePoint:
    time_minutes: int
    glucose_mg_dl: float


@dataclass(frozen=True)
class CGTCurve:
    baseline_mg_dl: float
    peak_mg_dl: float
    time_to_peak_min: int
    iauc_mg_dl_min: float
    glycemic_load: float
    curve: list[CurvePoint] = field(default_factory=list)
    model_id: str = MODEL_ID
    is_validated: bool = False
    disclaimer: str = DISCLAIMER

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_mg_dl": self.baseline_mg_dl,
            "peak_mg_dl": self.peak_mg_dl,
            "time_to_peak_min": self.time_to_peak_min,
            "iauc_mg_dl_min": self.iauc_mg_dl_min,
            "glycemic_load": self.glycemic_load,
            "curve": [{"time_minutes": p.time_minutes,
                       "glucose_mg_dl": p.glucose_mg_dl} for p in self.curve],
            "model_id": self.model_id,
            "is_validated": self.is_validated,
            "disclaimer": self.disclaimer,
        }


def glycemic_load(carbs_g: float, gi: float, servings: float = 1.0) -> float:
    """GL = available carbohydrate x GI / 100, per the standard definition."""
    return (carbs_g * gi / 100.0) * servings


def predict_curve(
    *,
    carbs_g: float,
    gi: float,
    fibre_g: float = 0.0,
    fat_g: float = 0.0,
    servings: float = 1.0,
    baseline_mg_dl: float = 95.0,
) -> CGTCurve:
    """Draw an illustrative postprandial curve. See the module docstring first.

    The curve is a gamma-like rise and fall, ``G(t) = baseline + peak * (t/tp) *
    exp(1 - t/tp)``, which peaks at ``tp`` and decays smoothly. That shape is a
    reasonable caricature of a postprandial excursion; the height and timing are not
    calibrated against anything.
    """
    gl = glycemic_load(carbs_g, gi, servings)

    peak_rise = max(
        MIN_PEAK_RISE_MG_DL,
        gl * PEAK_MG_DL_PER_GL_UNIT
        * math.exp(-FAT_DAMPING * fat_g)
        * math.exp(-FIBRE_DAMPING * fibre_g),
    )
    time_to_peak = int(
        BASE_TIME_TO_PEAK_MIN
        + min(MAX_FAT_DELAY_MIN, fat_g * FAT_DELAY_MIN_PER_G)
    )

    points: list[CurvePoint] = []
    iauc = 0.0
    previous = baseline_mg_dl
    for minute in range(0, _CURVE_MINUTES + _CURVE_STEP_MIN, _CURVE_STEP_MIN):
        if minute == 0:
            glucose = baseline_mg_dl
        else:
            relative = minute / float(time_to_peak)
            glucose = baseline_mg_dl + peak_rise * relative * math.exp(1.0 - relative)

        points.append(CurvePoint(minute, round(glucose, 1)))
        if minute > 0:
            # Incremental area over baseline, trapezoidal. iAUC by the "incremental"
            # convention: area below baseline is not subtracted.
            mean_height = (glucose + previous) / 2.0 - baseline_mg_dl
            iauc += max(0.0, mean_height) * _CURVE_STEP_MIN
        previous = glucose

    return CGTCurve(
        baseline_mg_dl=baseline_mg_dl,
        peak_mg_dl=round(baseline_mg_dl + peak_rise, 1),
        time_to_peak_min=time_to_peak,
        iauc_mg_dl_min=round(iauc, 1),
        glycemic_load=round(gl, 1),
        curve=points,
    )
