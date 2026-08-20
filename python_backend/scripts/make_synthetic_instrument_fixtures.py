#!/usr/bin/env python
"""Generate SYNTHETIC stand-ins for the instrument fixtures described in the
instrument-data rollout brief (Section 3).

The real fixtures (Luna ODiSI 6104 pass files + a Campbell ``.dat`` pressure
log) were NOT present on this machine when the parsers were built. This script
writes files that follow the WRITTEN description of those formats exactly --
same header keys, row kinds, gage count, x-axis range, timestep counts per pass,
sample counts, channel names, per-channel means / maxima -- so the parser
pipeline can be exercised end to end. Measurement rows are written TARED (the
real-file convention verified on pass 001), with a termination artifact in the
last three gages and 14 dead (NaN) gages. They are NOT the real recordings: the
strain / pressure signals are seeded synthetic traffic, and every claim in the
brief must still be re-verified against the real files with
``scripts/verify_parsers.py`` when they land.

Deterministic (fixed seed). Output layout (default ``tests/fixtures/synthetic``)::

    odisi/extraction_manifest.json
    odisi/ODiSI_6000_2026-04-09_16-18-51_ch3_pass_00{1,2,3,4}.tsv
    pressure/2024-05-10.dat
    README_SYNTHETIC.md

Usage:  python scripts/make_synthetic_instrument_fixtures.py [out_dir]
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np

# --- Spec constants (Section 3 of the brief) --------------------------------
GAGE_PITCH_MM = 2.6
SENSOR_LENGTH_M = 20.4542
RATE_HZ = 8.333
SENSOR_SERIAL = "FS02025LUNA0017736"
TARE_NAME = "0409"
N_GAGES = 7795
X_FIRST = 0.08
X_LAST = 20.4424
PASS_STEPS = (497, 534, 606, 600)  # timesteps per pass
PASS_T0 = datetime(2026, 4, 9, 16, 23, 46)  # first pass starts here
DT_S = 0.12  # 1 / 8.333 Hz
HEADER_LINES = 33
SOURCE_STEM = "ODiSI_6000_2026-04-09_16-18-51_ch3"
DEAD_GAGES = [151, 3640] + list(range(5740, 5752))  # 14 dead gages, as in the real pass 001

DAT_ROWS = 864_000  # 24 h at 10 Hz
DAT_T0 = datetime(2024, 5, 10, 0, 0, 0)
DAT_RECORD0 = 249671
DAT_CHANNELS = ("TP4144_kPa", "TP4145_kPa", "TP4148_kPa", "TP4149_kPa")
DAT_MEANS = (11.61, 9.93, 10.16, 7.54)
DAT_MAXES = (29.96, 27.87, 40.91, 39.87)


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _odisi_header(pass_name: str, t_start: datetime, t_end: datetime) -> list[str]:
    """33 tab-separated key/value header lines (plausible Luna ODiSI 6 keys)."""
    lines = [
        ("Test Name:", f"{SOURCE_STEM}_{pass_name}"),
        ("Product:", "ODiSI 6104"),
        ("Software Version:", "2.7.2 (synthetic stand-in)"),
        ("Interrogator Serial Number:", "SYNTH-0000"),
        ("Channel:", "3"),
        ("Sensor Name:", SENSOR_SERIAL),
        ("Sensor Serial Number:", SENSOR_SERIAL),
        ("Sensor Type:", "Strain"),
        ("Sensor Key:", "SYNTHETIC"),
        ("Sensor Length (m):", f"{SENSOR_LENGTH_M}"),
        ("Gage Pitch (mm):", f"{GAGE_PITCH_MM}"),
        ("Measurement Rate per Channel (Hz):", f"{RATE_HZ}"),
        ("Performance:", "Standard"),
        ("Tare Name:", TARE_NAME),
        ("Tare Date:", "2026-04-09 15:58:11"),
        ("Units:", "microstrain"),
        ("Data Range Start:", _fmt_ts(t_start)),
        ("Data Range End:", _fmt_ts(t_end)),
        ("Number of Measurements:", ""),  # left blank in the export
        ("Notes:", "SYNTHETIC file generated from the written spec - not a real recording"),
    ]
    # Pad with neutral keys so the header is exactly HEADER_LINES long, as the
    # brief describes (~33 header lines).
    i = 1
    while len(lines) < HEADER_LINES:
        lines.append((f"Reserved {i}:", ""))
        i += 1
    return [f"{k}\t{v}" for k, v in lines]


def write_odisi(out_dir: str, rng: np.random.Generator) -> None:
    os.makedirs(out_dir, exist_ok=True)
    x = np.round(np.linspace(X_FIRST, X_LAST, N_GAGES), 4)
    # Sanity: pitch implied by the linspace matches 2.6 mm to rounding.
    assert abs((x[-1] - x[0]) / (N_GAGES - 1) * 1000 - GAGE_PITCH_MM) < 0.02
    # Tare row: a large, slowly varying locked-in baseline like the real file
    # (which spans thousands of microstrain); measurement rows are tared.
    tare = 900.0 * np.sin(x / 3.1) + 400.0 * np.cos(x * 1.7) + rng.normal(0, 1.0, N_GAGES)

    manifest_passes = []
    global_row = 0
    t = PASS_T0
    for idx, n_steps in enumerate(PASS_STEPS, start=1):
        pass_name = f"pass_{idx:03d}"
        fname = f"{SOURCE_STEM}_{pass_name}.tsv"
        stamps = [t + timedelta(seconds=DT_S * k) for k in range(n_steps)]
        header = _odisi_header(pass_name, stamps[0], stamps[-1])
        # A two-axle vehicle crossing the 20 m fibre at 1.6-2.4 m/s during the
        # middle of the pass; the road bends under each axle.
        speed = 1.6 + 0.2 * idx
        t_enter = 0.25 * n_steps * DT_S
        amp = 90.0 + 15.0 * idx
        path = os.path.join(out_dir, fname)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(header) + "\n")
            fh.write("-" * 40 + "\n")
            fh.write("Tare\t\t\t" + "\t".join(f"{v:.3f}" for v in tare) + "\n")
            fh.write("x-axis\t\t\t" + "\t".join(f"{v:.4f}" for v in x) + "\n")
            for k in range(n_steps):
                tk = k * DT_S
                xl = -3.0 + speed * (tk - t_enter)
                bend = amp * np.exp(-((x - xl) / 0.55) ** 2) + 0.8 * amp * np.exp(
                    -((x - (xl - 4.2)) / 0.55) ** 2
                )
                # Real-file convention (verified 2026-08-18 on pass 001): the
                # ODiSI writes measurement rows ALREADY relative to the named
                # tare -- the Tare row is the reference baseline, NOT added in.
                row = bend + rng.normal(0, 1.5, N_GAGES)
                # Termination artifact at the fibre tail (as in the real file:
                # the last gages read tens of thousands of microstrain) and a
                # handful of dead gages (NaN throughout).
                row[-3:] = np.array([11267.6, 7772.4, -15405.8]) * (0.8 + 0.1 * idx)
                row[DEAD_GAGES] = np.nan
                fh.write(
                    _fmt_ts(stamps[k])
                    + "\tmeasurement\t\t"
                    + "\t".join(f"{v:.3f}" for v in row)
                    + "\n"
                )
        manifest_passes.append(
            {
                "name": pass_name,
                "file": fname,
                "start": global_row,  # inclusive measurement-row index in the source
                "end": global_row + n_steps,  # exclusive
                "n_timesteps": n_steps,
                "first_timestamp": _fmt_ts(stamps[0]),
                "last_timestamp": _fmt_ts(stamps[-1]),
            }
        )
        global_row += n_steps
        t = stamps[-1] + timedelta(seconds=DT_S)
        print(f"wrote {path} ({os.path.getsize(path) / 1e6:.1f} MB, {n_steps} timesteps)")

    manifest = {
        "synthetic": True,
        "note": (
            "SYNTHETIC manifest generated from the written spec. start is "
            "inclusive, end is exclusive (row indices into the source file's "
            "measurement rows)."
        ),
        "source_file": f"{SOURCE_STEM}.tsv",
        "header_lines": HEADER_LINES,
        "boundary_convention": "start_inclusive_end_exclusive",
        "passes": manifest_passes,
    }
    with open(os.path.join(out_dir, "extraction_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print("wrote extraction_manifest.json")


def write_campbell(out_dir: str, rng: np.random.Generator) -> None:
    os.makedirs(out_dir, exist_ok=True)
    n = DAT_ROWS
    n_ch = len(DAT_CHANNELS)
    t_s = np.arange(n) * 0.1  # seconds since midnight

    # Traffic events: more during the day, each a short pressure pulse whose
    # peaks arrive at the four cells in line (direction of travel).
    hours = t_s / 3600.0
    events = 0.0 * t_s
    per_ch = np.zeros((n, n_ch), dtype=np.float64)
    n_events = 340
    hour_weights = np.array([0.2, 0.15, 0.1, 0.1, 0.15, 0.4, 0.9, 1.4, 1.6, 1.4, 1.3, 1.3,
                             1.4, 1.3, 1.4, 1.6, 1.8, 1.7, 1.3, 0.9, 0.7, 0.5, 0.4, 0.3])
    hour_p = hour_weights / hour_weights.sum()
    ev_hours = rng.choice(24, size=n_events, p=hour_p) + rng.random(n_events)
    ev_t = np.sort(ev_hours * 3600.0)
    ev_amp = rng.lognormal(mean=np.log(9.0), sigma=0.45, size=n_events)
    ev_width = rng.uniform(0.35, 0.9, size=n_events)
    ev_dir = rng.random(n_events) < 0.55  # True -> travels cell 0 -> 3
    ch_scale = np.array([1.0, 0.95, 1.55, 1.6])
    for te, amp, w, forward in zip(ev_t, ev_amp, ev_width, ev_dir):
        i0 = int(max(0, (te - 5 * w - 3) * 10))
        i1 = int(min(n, (te + 5 * w + 3) * 10))
        seg = t_s[i0:i1]
        for c in range(n_ch):
            lag = (c if forward else (n_ch - 1 - c)) * 0.45  # 0.45 s between cells
            per_ch[i0:i1, c] += amp * ch_scale[c] * np.exp(-((seg - te - lag) / w) ** 2)
    noise = rng.normal(0, 0.12, size=(n, n_ch))
    sig = per_ch + noise
    # Fit baseline + scale so per-channel mean/max hit the spec values exactly.
    out = np.empty_like(sig)
    for c in range(n_ch):
        s = sig[:, c]
        s_mean, s_max = s.mean(), s.max()
        # baseline b + k*s : mean = b + k*s_mean = M ; max = b + k*s_max = X
        k = (DAT_MAXES[c] - DAT_MEANS[c]) / (s_max - s_mean)
        b = DAT_MEANS[c] - k * s_mean
        out[:, c] = b + k * s
    out = np.round(out, 3)

    path = os.path.join(out_dir, "2024-05-10.dat")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("TIMESTAMP,RECORD," + ",".join(DAT_CHANNELS) + "\n")
        base = DAT_T0
        for i in range(n):
            ts = base + timedelta(milliseconds=100 * i)
            fh.write(
                f"{ts.strftime('%Y-%m-%d %H:%M:%S')}.{ts.microsecond // 1000:03d},"
                f"{DAT_RECORD0 + i},"
                + ",".join(f"{v:.3f}" for v in out[i])
                + "\n"
            )
    print(f"wrote {path} ({os.path.getsize(path) / 1e6:.1f} MB, {n} rows)")
    print("  column means:", np.round(out.mean(axis=0), 3).tolist())
    print("  column maxes:", np.round(out.max(axis=0), 3).tolist())


README = """# SYNTHETIC instrument fixtures

These files were generated by `scripts/make_synthetic_instrument_fixtures.py`
because the real ODiSI / Campbell fixtures were not on this machine when the
instrument parsers were built. They follow the written format description
(header keys, row kinds, gage count 7795, x-axis 0.08 -> 20.4424, timesteps
497/534/606/600, 864,000 pressure rows at 10 Hz, channel names, per-channel
means and maxima) but the SIGNALS ARE SEEDED SYNTHETIC TRAFFIC.

Do not treat any engineering number derived from them as real. Re-run
`scripts/verify_parsers.py <file>` against the real recordings when available.
"""


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(os.path.dirname(here), "tests", "fixtures", "synthetic")
    out = sys.argv[1] if len(sys.argv) > 1 else default_out
    rng = np.random.default_rng(20260409)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "README_SYNTHETIC.md"), "w", encoding="utf-8") as fh:
        fh.write(README)
    write_odisi(os.path.join(out, "odisi"), rng)
    write_campbell(os.path.join(out, "pressure"), rng)
    return 0


if __name__ == "__main__":
    sys.exit(main())
