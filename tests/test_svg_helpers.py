"""Simple sanity tests for :mod:`svg_helpers`.

The original project relied heavily on these helper functions for
string formatting and SVG generation.  The tests verify that the
functions behave as expected.
"""

import pytest
from svg_helpers import plain, signed, pretty


def test_plain_truncation():
    assert plain("Hello world", max=5) == "Hello…"
    assert plain("Short", max=10) == "Short"


def test_signed():
    assert signed("5") == "+5"
    assert signed("-3") == "-3"
    assert signed("0") == "+0"


def test_pretty():
    assert pretty("example_value") == "Example Value"
    assert pretty("already nice") == "Already nice"