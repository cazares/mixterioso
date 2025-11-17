# tests/test_manual_timing_write.py

import csv
from scripts.manual_write_csv import write_manual_csv  # you will create this small helper

def test_manual_csv_writes_end_equals_start_plus_epsilon(tmp_path):
    out = tmp_path / "man.csv"

    lyrics = ["line 1", "line 2"]
    timings = [
        {"line_index": 0, "time": 10.0, "text": "line 1"},
        {"line_index": 1, "time": 20.0, "text": "line 2"},
    ]

    write_manual_csv(out, lyrics, timings)

    with out.open() as f:
        r = list(csv.reader(f))

    assert r[0] == ["line_index","start_secs","end_secs","text"]
    assert r[1] == ["0","10.000","10.010","line 1"]
    assert r[2] == ["1","20.000","20.010","line 2"]
