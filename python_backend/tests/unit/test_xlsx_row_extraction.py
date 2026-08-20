"""Row-structured spreadsheet extraction (v3-xlsx) — file_processing.

Pins the contract that fixed the flattened-cell-list bug: one line per
non-empty row, columns in order, blanks positional, no dedup, readable
dates, header restated per block, capped with an explicit truncation note,
and formula cells falling back to their formula text when the workbook
carries no cached values.
"""
import datetime as dt
import io

import openpyxl
import pytest

from app.services import file_processing as fp
from app.services.rag_service import chunk_text_v2


def _wb_bytes(build):
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _booking_wb(wb):
    ws = wb.active
    ws.title = "Booking"
    ws.append(["S. No.", "Instrument Name", "Booked By", "Start Date", "End Date",
               "Purpose", "Remarks"])
    ws.append([1, "Interrogator", "Saeed", dt.datetime(2026, 1, 15),
               dt.datetime(2026, 1, 20), "TIP", None])
    ws.append([2, "Interrogator", "Jiming", dt.datetime(2026, 2, 3), None,
               "MTS tests with Geogrid", None])
    ws.append([])  # fully empty row must be skipped
    ws.append([3, "MTS", "Yongxuan", None, None, None, "2 bare DFOS"])


def test_rows_stay_associated_no_dedup():
    pages = fp.extract_pages_from_xlsx(_wb_bytes(_booking_wb), "b.xlsx")
    assert len(pages) == 1
    text = pages[0][1]
    lines = text.splitlines()
    assert lines[0] == "## Sheet: Booking"
    # header first, then one line per non-empty row, columns in order
    assert "| S. No. | Instrument Name | Booked By |" in lines[1]
    assert "| 1 | Interrogator | Saeed | 2026-01-15 | 2026-01-20 | TIP |  |" in text
    # blank cells preserved positionally; repeated values NOT deduplicated
    assert "| 2 | Interrogator | Jiming | 2026-02-03 |  | MTS tests with Geogrid |  |" in text
    assert "| 3 | MTS | Yongxuan |  |  |  | 2 bare DFOS |" in text
    assert text.count("Interrogator") == 2
    # empty row skipped: header + 3 data rows only
    assert sum(1 for ln in lines if ln.startswith("| ")) == 4


def test_dates_and_numbers_readable():
    assert fp._format_cell(dt.datetime(2026, 1, 15)) == "2026-01-15"
    assert fp._format_cell(dt.datetime(2026, 1, 15, 10, 30)) == "2026-01-15 10:30"
    assert fp._format_cell(dt.time(9, 0)) == "09:00"
    assert fp._format_cell(5.0) == "5"
    assert fp._format_cell(12.5) == "12.5"
    assert fp._format_cell(True) == "TRUE"
    assert fp._format_cell(None) == ""
    assert fp._format_cell("a\nb") == "a; b"        # newline can't break the row line
    assert fp._format_cell("a|b") == "a/b"          # pipe can't break the columns


def test_trailing_empty_columns_trimmed():
    def build(wb):
        ws = wb.active
        ws.title = "S"
        ws.append(["a", "b", None, None, None, None])
        ws.append([1, 2, None, None, None, None])
    text = fp.extract_pages_from_xlsx(_wb_bytes(build), "t.xlsx")[0][1]
    assert "| a | b |" in text and "| 1 | 2 |" in text
    assert "| a | b |  |" not in text


def test_cap_notes_truncation():
    rows = [["id", "v"]] + [[i, f"row {i}"] for i in range(1, 100)]
    text, meta = fp._render_sheet_rows("Big", rows, max_rows=40)
    assert meta["rows"] == 40 and meta["truncated_rows"] == 59
    assert "[Truncated: 59 additional non-empty row(s)" in text
    assert "XLSX_MAX_ROWS_PER_SHEET" in text


def test_header_restated_per_block_and_blocks_fit_chunker():
    rows = [["S. No.", "Name", "Owner", "Purpose"]] + [
        [i, f"Instrument {i}", f"Person {i}", f"Purpose number {i}"] for i in range(1, 300)]
    text, _ = fp._render_sheet_rows("Booking", rows)
    blocks = text.split("\n\n")
    assert len(blocks) > 1
    assert all("| S. No. | Name | Owner | Purpose |" in b for b in blocks)
    assert all(b.splitlines()[0].startswith("## Sheet: Booking") for b in blocks)
    # each block must fit the v2 chunker's max fragment so it is never split
    assert all(len(b) <= 1500 for b in blocks)
    # end to end: every v2 chunk of this sheet carries the column header
    chunks = chunk_text_v2([(1, text)])
    assert len(chunks) > 1
    assert all("| S. No. | Name | Owner | Purpose |" in c["text"] for c in chunks)


def test_formula_fallback_when_no_cached_value():
    def build(wb):
        ws = wb.active
        ws.title = "Dash"
        ws.append(["Who", "Hours"])
        ws.append(["Shane", '=SUMIFS(Log!B:B,Log!A:A,"Shane")'])
    pages, meta = fp.extract_xlsx_sheets(_wb_bytes(build), "f.xlsx")
    text = pages[0][1]
    # openpyxl saves no cached value: the cell must show the formula, not vanish
    assert '| Shane | =SUMIFS(Log!B:B,Log!A:A,"Shane") |' in text
    assert meta["formula_fallback_cells"] == 1


def test_csv_rows_kept_verbatim_with_quoting():
    data = ('name,qty,notes\n'
            'Interrogator,1,"MTS, with Geogrid"\n'
            'DFOS,4,\n')
    pages = fp.extract_pages_from_csv(data.encode(), "inv.csv")
    text = pages[0][1]
    assert "| name | qty | notes |" in text
    assert "| Interrogator | 1 | MTS, with Geogrid |" in text
    assert "| DFOS | 4 |" in text


def test_empty_sheet_skipped_entirely():
    def build(wb):
        wb.active.title = "Empty"
        ws2 = wb.create_sheet("Data")
        ws2.append(["a"])
        ws2.append(["b"])
    pages = fp.extract_pages_from_xlsx(_wb_bytes(build), "e.xlsx")
    assert [p[0] for p in pages] == [2]
    assert pages[0][1].startswith("## Sheet: Data")
