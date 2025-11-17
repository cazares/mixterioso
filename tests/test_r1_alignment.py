# tests/test_r1_alignment.py

from pathlib import Path
import tempfile

from scripts.aligner_r1 import (
    align_lines_r1,
    Word,
)

def test_r1_basic_alignment(sample_lines, sample_words):
    words = [Word(text=w, start=s, end=e) for w, s, e in sample_words]

    aligned = align_lines_r1(sample_lines, words)

    assert len(aligned) == 2

    # First line should align near t=0.0
    assert abs(aligned[0].start_secs - 0.00) < 0.05
    assert aligned[0].end_secs > aligned[0].start_secs

    # Second line should find the second "hello" at t≈5.0
    assert aligned[1].start_secs >= 4.9
    assert aligned[1].end_secs > aligned[1].start_secs


def test_r1_monotonicity(sample_lines, sample_words):
    words = [Word(text=w, start=s, end=e) for w, s, e in sample_words]
    aligned = align_lines_r1(sample_lines, words)

    # timestamps strictly increasing
    assert aligned[0].start_secs < aligned[1].start_secs
    assert aligned[0].end_secs <= aligned[1].start_secs


def test_r1_repeated_line_forward_progression():
    lines = ["hello", "hello", "hello"]

    # three identical hello words spaced apart
    sample = [
        Word("hello", 0.0, 0.4),
        Word("hello", 1.0, 1.4),
        Word("hello", 2.0, 2.4),
    ]

    aligned = align_lines_r1(lines, sample)
    starts = [tr.start_secs for tr in aligned]

    # Should map to 0.0, 1.0, 2.0 in order
    assert starts == sorted(starts)
    assert starts[1] > starts[0]
    assert starts[2] > starts[1]
