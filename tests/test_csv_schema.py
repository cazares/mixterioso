# tests/test_csv_schema.py

import csv
from scripts.aligner_r1 import write_csv_4col, AlignedLine
from pathlib import Path

def test_csv_structure(tmp_path):
    out = tmp_path / "test.csv"

    rows = [
        AlignedLine(0, 1.23, 2.34, "Line One"),
        AlignedLine(1, 3.00, 4.00, "Line Two"),
    ]

    write_csv_4col(out, rows)

    with out.open() as f:
        r = list(csv.reader(f))

    assert r[0] == ["line_index", "start_secs", "end_secs", "text"]
    assert r[1] == ["0", "1.230", "2.340", "Line One"]
    assert r[2] == ["1", "3.000", "4.000", "Line Two"]
