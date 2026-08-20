"""Unit tests for the instrument parsers (ODiSI TSV + Campbell .dat) and the
parser registry. Small handcrafted files only -- the large fixtures are
exercised by scripts/verify_parsers.py."""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.workspace.parsers import registry
from app.workspace.parsers.base import SNIFF_BYTES, ParserError
from app.workspace.parsers.odisi import PARSER_ID as ODISI_ID, parse_odisi, sniff_odisi


# --- helpers ----------------------------------------------------------------
def _odisi_text(
    n_gages: int = 5,
    n_steps: int = 3,
    tare_len: int | None = None,
    ragged_step: int | None = None,
    declared_gages: int | None = None,
    separator: bool = True,
    value_col: int = 3,
) -> str:
    header = [
        "Test Name:\tunit",
        "Product:\tODiSI 6104",
        "Sensor Serial Number:\tFS02025LUNA0017736",
        "Sensor Length (m):\t20.4542",
        "Gage Pitch (mm):\t2.6",
        "Measurement Rate per Channel (Hz):\t8.333",
        "Tare Name:\t0409",
        "Units:\tmicrostrain",
    ]
    if declared_gages is not None:
        header.append(f"Number of Gages:\t{declared_gages}")
    lines = list(header)
    if separator:
        lines.append("-" * 20)
    pad = "\t" * value_col  # values start at index value_col
    x = [0.08 + 0.0026 * i for i in range(n_gages)]
    tare_n = n_gages if tare_len is None else tare_len
    lines.append("Tare" + pad + "\t".join(f"{1.0 + i:.3f}" for i in range(tare_n)))
    lines.append("x-axis" + pad + "\t".join(f"{v:.4f}" for v in x))
    for k in range(n_steps):
        n = n_gages if ragged_step != k else n_gages - 2
        vals = "\t".join(f"{10 * k + i:.3f}" for i in range(n))
        lines.append(f"2026-04-09 16:23:{46 + k:02d}.000\tmeasurement" + "\t" * (value_col - 1) + vals)
    return "\n".join(lines) + "\n"


def _write(tmp_path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- registry ---------------------------------------------------------------
def test_registry_has_odisi_and_sniff_precedence():
    ids = [p.id for p in registry.all_parsers()]
    assert ODISI_ID in ids
    assert registry.get(ODISI_ID).dataset_kind == "strain_distributed"
    assert registry.get("nope") is None


def test_sniff_is_pure_over_first_2kb_and_returns_none_for_documents():
    assert registry.sniff(b"") is None
    assert registry.sniff(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj") is None
    assert registry.sniff(b"depth_m,spt_n,soil\n0.5,4,silty sand\n") is None
    assert registry.sniff(b"$\nHA=1,HB=2,MA=0.80\n#\nD=0.0,QC=1,FS=2,U=3\n") is None
    head = _odisi_text().encode()[:SNIFF_BYTES]
    assert registry.sniff(head) == ODISI_ID
    # More than 2 KB passed -> only the first 2 KB matter (same answer).
    assert registry.sniff(_odisi_text(n_gages=2000).encode()) == ODISI_ID


def test_sniff_odisi_requires_tab_key_value_structure():
    assert sniff_odisi(b"ODiSI is a product made by Luna. Gage pitch is 2.6 mm.") is False
    assert sniff_odisi(b"Gage Pitch (mm):\t2.6\nSensor Length (m):\t20\nTare Name:\t0409\n") is True
    assert sniff_odisi(b"Product:\tODiSI 6104\n") is True  # odisi + one key


# --- ODiSI parse ------------------------------------------------------------
def test_parse_odisi_clean_file(tmp_path):
    path = _write(tmp_path, "pass.tsv", _odisi_text(n_gages=5, n_steps=3))
    r = parse_odisi(path)
    assert r.parser_id == ODISI_ID and r.dataset_kind == "strain_distributed"
    assert r.arrays["x_axis"].shape == (5,) and r.arrays["x_axis"].dtype == np.float64
    assert r.arrays["tare"].shape == (5,) and r.arrays["tare"].dtype == np.float64
    assert r.arrays["strain"].shape == (3, 5) and r.arrays["strain"].dtype == np.float32
    assert r.arrays["timestamps"].dtype == np.dtype("datetime64[ms]")
    assert r.arrays["timestamps"][0] == np.datetime64("2026-04-09T16:23:46.000")
    assert r.metadata["n_gages"] == 5 and r.metadata["n_timesteps"] == 3
    assert r.metadata["gage_pitch_mm"] == 2.6
    assert r.metadata["sensor_length_m"] == 20.4542
    assert r.metadata["measurement_rate_hz"] == 8.333
    assert r.metadata["sensor_serial"] == "FS02025LUNA0017736"
    assert r.metadata["tare_name"] == "0409"  # label, not the int 409
    assert r.metadata["units"] == "microstrain"
    assert r.metadata["header_line_count"] == 8
    assert r.metadata["_raw_header"][0] == "Test Name:\tunit"
    assert r.metadata["x_min_m"] == pytest.approx(0.08)
    assert r.warnings == []
    # strain is stored as recorded (absolute), values from column 3
    assert float(r.arrays["strain"][1, 2]) == pytest.approx(12.0)


def test_parse_odisi_without_separator_line(tmp_path):
    path = _write(tmp_path, "pass.tsv", _odisi_text(separator=False))
    r = parse_odisi(path)
    assert r.arrays["strain"].shape == (3, 5) and r.warnings == []


def test_parse_odisi_warns_on_tare_length_mismatch(tmp_path):
    path = _write(tmp_path, "pass.tsv", _odisi_text(n_gages=5, tare_len=4))
    r = parse_odisi(path)
    assert r.arrays["tare"].shape == (5,)
    assert np.isnan(r.arrays["tare"][-1])
    assert any("Tare length 4" in w for w in r.warnings)


def test_parse_odisi_warns_on_ragged_measurement_row(tmp_path):
    path = _write(tmp_path, "pass.tsv", _odisi_text(n_gages=6, n_steps=4, ragged_step=2))
    r = parse_odisi(path)
    assert r.arrays["strain"].shape == (4, 6)
    assert np.isnan(r.arrays["strain"][2, -1])
    assert any("1 measurement row(s) had a column count" in w for w in r.warnings)


def test_parse_odisi_warns_on_header_gage_count_contradiction(tmp_path):
    path = _write(tmp_path, "pass.tsv", _odisi_text(n_gages=5, declared_gages=7795))
    r = parse_odisi(path)
    assert any("Header declares 7795 gages" in w and "5" in w for w in r.warnings)


def test_parse_odisi_auto_detects_value_column_with_warning(tmp_path):
    # Values starting at column 4 (an extra empty label column) instead of the
    # documented column 3: the x-axis row's column 3 is not numeric, so the
    # parser auto-detects the value column and says so.
    path = _write(tmp_path, "pass.tsv", _odisi_text(n_gages=5, value_col=4))
    r = parse_odisi(path)
    assert r.arrays["strain"].shape == (3, 5)
    assert r.arrays["x_axis"].shape == (5,)
    assert any("auto-detected column 4" in w for w in r.warnings)


def test_parse_odisi_raises_on_non_odisi_file(tmp_path):
    path = _write(tmp_path, "junk.tsv", "Key:\tvalue\nOther:\tthing\n")
    with pytest.raises(ParserError):
        parse_odisi(path)


def test_parse_odisi_reports_progress_monotonically(tmp_path):
    path = _write(tmp_path, "pass.tsv", _odisi_text(n_gages=50, n_steps=40))
    seen = []
    parse_odisi(path, progress=seen.append)
    assert seen and seen[-1] == 1.0
    assert all(b >= a for a, b in zip(seen, seen[1:]))
    assert os.path.getsize(path) > 0


# --- Campbell .dat ------------------------------------------------------------
from app.workspace.parsers.campbell import (  # noqa: E402
    PARSER_ID as CAMPBELL_ID,
    parse_campbell,
    sniff_campbell,
)


def _campbell_text(
    channels=("TP4144_kPa", "TP4145_kPa", "TP4148_kPa", "TP4149_kPa"),
    n: int = 20,
    toa5: bool = False,
    quoted: bool = False,
    extra_col: bool = False,
    bad_row: bool = False,
    nan_token: bool = False,
    record_gap: bool = False,
    time_gap: bool = False,
) -> str:
    cols = ["TIMESTAMP", "RECORD"] + list(channels) + (["BattV"] if extra_col else [])
    lines = []
    if toa5:
        lines.append('"TOA5","CR1000X","CR1000X","1234","CR1000X.Std.05","CPU:prog.CR1X","1111","Table"')
    lines.append(",".join(f'"{c}"' if quoted else c for c in cols))
    if toa5:
        lines.append(",".join(['"TS"', '"RN"'] + ['"kPa"'] * len(channels) + (['"V"'] if extra_col else [])))
        lines.append(",".join(['""', '""'] + ['"Smp"'] * len(channels) + (['"Smp"'] if extra_col else [])))
    rec = 249671
    ms = 0
    for i in range(n):
        if record_gap and i == 10:
            rec += 5
        if time_gap and i == 10:
            ms += 5000
        ts = f"2024-05-10 00:00:{ms // 1000:02d}.{ms % 1000:03d}"
        vals = [f"{10.0 + c + 0.1 * i:.3f}" for c in range(len(channels))]
        if nan_token and i == 3:
            vals[0] = "NAN"
        row = [f'"{ts}"' if quoted else ts, str(rec)] + vals + (["12.5"] if extra_col else [])
        if bad_row and i == 5:
            row = row[:-1]  # one field short
        lines.append(",".join(row))
        rec += 1
        ms += 100
    return "\n".join(lines) + "\n"


def test_registry_has_campbell_after_odisi():
    ids = [p.id for p in registry.all_parsers()]
    assert ids.index(ODISI_ID) < ids.index(CAMPBELL_ID)
    assert registry.get(CAMPBELL_ID).dataset_kind == "pressure_timeseries"


def test_sniff_campbell_requires_timestamp_record_and_kpa():
    assert sniff_campbell(_campbell_text().encode()[:SNIFF_BYTES]) is True
    assert sniff_campbell(_campbell_text(toa5=True, quoted=True).encode()[:SNIFF_BYTES]) is True
    # generic CSVs never match
    assert sniff_campbell(b"TIMESTAMP,RECORD,Temp_C\n2024-05-10 00:00:00,1,20.1\n") is False
    assert sniff_campbell(b"TIMESTAMP,TP4144_kPa\n2024-05-10 00:00:00,11.2\n") is False
    assert sniff_campbell(b"depth_m,spt_n,soil\n0.5,4,silty sand\n") is False
    assert sniff_campbell(b"%PDF-1.7\n") is False
    assert registry.sniff(_campbell_text().encode()) == CAMPBELL_ID
    assert registry.sniff(_odisi_text().encode()) == ODISI_ID


def test_parse_campbell_clean_file_detects_channels_dynamically(tmp_path):
    path = _write(tmp_path, "2024-05-10.dat", _campbell_text(n=20))
    r = parse_campbell(path)
    assert r.parser_id == CAMPBELL_ID and r.dataset_kind == "pressure_timeseries"
    assert r.metadata["channel_names"] == ["TP4144_kPa", "TP4145_kPa", "TP4148_kPa", "TP4149_kPa"]
    assert r.metadata["n_channels"] == 4 and r.metadata["n_samples"] == 20
    assert r.arrays["pressure"].shape == (20, 4) and r.arrays["pressure"].dtype == np.float32
    assert r.arrays["record"].dtype == np.int64 and int(r.arrays["record"][0]) == 249671
    assert r.arrays["timestamps"].dtype == np.dtype("datetime64[ms]")
    assert r.metadata["first_timestamp"] == "2024-05-10T00:00:00.000"
    assert r.metadata["last_timestamp"] == "2024-05-10T00:00:01.900"
    assert r.metadata["sample_rate_hz"] == 10.0
    assert r.metadata["record_first"] == 249671 and r.metadata["record_last"] == 249690
    assert r.metadata["column_max"][3] == pytest.approx(13.0 + 1.9, abs=1e-3)
    assert r.metadata["_raw_header"] == ["TIMESTAMP,RECORD,TP4144_kPa,TP4145_kPa,TP4148_kPa,TP4149_kPa"]
    assert r.warnings == []


def test_parse_campbell_two_and_six_channels(tmp_path):
    two = _write(tmp_path, "two.dat", _campbell_text(channels=("A_kPa", "B_kPa")))
    six = _write(tmp_path, "six.dat", _campbell_text(channels=tuple(f"C{i}_kPa" for i in range(6))))
    assert parse_campbell(two).arrays["pressure"].shape == (20, 2)
    r6 = parse_campbell(six)
    assert r6.arrays["pressure"].shape == (20, 6)
    assert r6.metadata["channel_names"] == [f"C{i}_kPa" for i in range(6)]


def test_parse_campbell_toa5_quoted_with_extra_column(tmp_path):
    path = _write(tmp_path, "toa5.dat", _campbell_text(toa5=True, quoted=True, extra_col=True))
    r = parse_campbell(path)
    assert r.arrays["pressure"].shape == (20, 4)
    assert r.metadata["other_columns"] == ["BattV"]
    assert r.metadata["header_line_index"] == 1
    assert len(r.metadata["_raw_header"]) == 4  # TOA5 line, names, units, processing
    assert r.warnings == []


def test_parse_campbell_warns_on_bad_row_nan_record_and_time_gaps(tmp_path):
    path = _write(
        tmp_path, "messy.dat",
        _campbell_text(bad_row=True, nan_token=True, record_gap=True, time_gap=True),
    )
    r = parse_campbell(path)
    assert r.arrays["pressure"].shape == (19, 4)  # one row skipped
    assert np.isnan(r.arrays["pressure"][3, 0])  # NAN token
    joined = " | ".join(r.warnings)
    assert "1 row(s) skipped for a wrong field count" in joined
    assert "RECORD is not strictly sequential" in joined
    assert "gap(s) in the time series" in joined


def test_parse_campbell_raises_on_non_campbell_and_empty(tmp_path):
    with pytest.raises(ParserError):
        parse_campbell(_write(tmp_path, "x.csv", "depth_m,spt_n\n0.5,4\n"))
    with pytest.raises(ParserError):
        parse_campbell(_write(tmp_path, "y.dat", "TIMESTAMP,RECORD,TP1_kPa\n"))


def test_parse_campbell_progress(tmp_path):
    path = _write(tmp_path, "p.dat", _campbell_text(n=500))
    seen = []
    parse_campbell(path, progress=seen.append)
    assert seen[-1] == 1.0 and all(b >= a for a, b in zip(seen, seen[1:]))
