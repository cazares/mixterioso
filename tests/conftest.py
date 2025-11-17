# tests/conftest.py

import sys
from pathlib import Path
import pytest

# -------------------------------------------------------------------
# Make project root importable
# -------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# -------------------------------------------------------------------
# Fixtures required by test_r1_alignment.py
# -------------------------------------------------------------------

@pytest.fixture
def sample_lines():
    """
    Lines expected by tests:
    ['hello world', 'foo bar', 'the quick brown fox']
    """
    return [
        "hello world",
        "foo bar",
        "the quick brown fox",
    ]


@pytest.fixture
def sample_words():
    """
    Creates a list of Word(word, start, end)
    for each token across the sample_lines.

    This matches the test's expectations exactly.
    """
    from scripts.aligner_r1 import Word

    words = [
        Word(word="hello", start=0.0, end=0.4),
        Word(word="world", start=0.4, end=0.8),

        Word(word="foo",   start=1.0, end=1.3),
        Word(word="bar",   start=1.3, end=1.6),

        Word(word="the",   start=2.0, end=2.2),
        Word(word="quick", start=2.2, end=2.5),
        Word(word="brown", start=2.5, end=2.7),
        Word(word="fox",   start=2.7, end=3.0),
    ]

    return words
