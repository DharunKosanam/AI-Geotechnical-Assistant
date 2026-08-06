"""Tests for the CPTLOG parser.

Real .CPT files are CPTLOG key=value, not CSV. These tests pin the parser to the
real format using the exact anchors from the reference sounding 040C6.CPT
(header MA=0.580; first reading D=0.000,QC=2.690,FS=50,U=165), plus robustness
cases (blank lines, optional/extra keys, missing markers).
"""

import pytest

from app.workspace.parsers.cpt import (
    DEFAULT_AREA_RATIO,
    parse_cpt,
    parse_cpt_text,
)
from app.workspace.data import SAMPLE_CPT_FILE

# A CPTLOG excerpt built from the real 040C6.CPT header + first rows.
REAL_EXCERPT = """$
HA=1,HB=2,HC=CPTLOG-1.00,GA=Site,MA=0.580,ZZ=9
#
D=0.000,QC=2.690,FS=50,U=165,TA=4.7,B=0
D=0.020,QC=2.710,FS=51,U=166

D=0.040,QC=2.700,FS=52,U=167,NA=1,NB=2,NC=3
"""


def test_area_ratio_from_MA():
    s = parse_cpt_text(REAL_EXCERPT, source_name="040C6.CPT")
    assert s.area_ratio == pytest.approx(0.580)
    assert s.area_ratio_source == "MA"


def test_first_reading_mapped_correctly():
    s = parse_cpt_text(REAL_EXCERPT)
    r0 = s.rows[0]
    assert (r0.z, r0.qc, r0.fs, r0.u2) == (0.000, 2.690, 50.0, 165.0)


def test_header_pairs_parsed_into_dict():
    s = parse_cpt_text(REAL_EXCERPT)
    assert s.header["HC"] == "CPTLOG-1.00"
    assert s.header["MA"] == "0.580"


def test_blank_lines_and_extra_keys_are_tolerated():
    s = parse_cpt_text(REAL_EXCERPT)
    # 3 readings despite a blank line and extra TA/B/NA/NB/NC keys.
    assert len(s.rows) == 3
    # Extra keys are ignored, not required.
    assert [round(r.z, 3) for r in s.rows] == [0.0, 0.02, 0.04]


def test_start_and_data_markers_ignored():
    s = parse_cpt_text(REAL_EXCERPT)
    # '$' and '#' never become readings or header keys.
    assert "$" not in s.header and "#" not in s.header


def test_missing_MA_falls_back_to_default():
    text = "$\nHA=1,HB=2\n#\nD=0.0,QC=1.0,FS=10,U=5\n"
    s = parse_cpt_text(text)
    assert s.area_ratio == pytest.approx(DEFAULT_AREA_RATIO)
    assert s.area_ratio_source == "default"


def test_reading_without_data_marker_still_parses():
    # Robust to a file that omits the '#' marker: a line with D and QC is data.
    text = "MA=0.60\nD=0.0,QC=1.0,FS=10,U=5\nD=0.2,QC=1.1,FS=11,U=6\n"
    s = parse_cpt_text(text)
    assert len(s.rows) == 2
    assert s.area_ratio == pytest.approx(0.60)


def test_optional_u_channel_defaults_to_zero():
    text = "$\nMA=0.58\n#\nD=0.0,QC=1.0,FS=10\n"
    s = parse_cpt_text(text)
    assert s.rows[0].u2 == 0.0


def test_no_readings_raises():
    with pytest.raises(ValueError):
        parse_cpt_text("$\nHA=1\n#\n")


def test_csv_format_is_rejected():
    # The old CSV fixture format must NOT silently parse as CPTLOG.
    with pytest.raises(ValueError):
        parse_cpt_text("z,qc,fs,u2\n0.2,0.4,27,8\n")


def test_parse_cpt_reads_the_cptlog_fixture_file():
    # The on-disk fixture is now CPTLOG and parses via the path API.
    s = parse_cpt(SAMPLE_CPT_FILE)
    assert s.area_ratio_source == "MA"
    assert len(s.rows) > 0
