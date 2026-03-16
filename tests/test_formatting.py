"""Tests for formatting helpers."""

import math

import pandas as pd

from formatting import fmt_currency, fmt_pct, fmt_int, fc, fp, fi, to_py, jslist, esc


class TestFmtCurrency:
    def test_positive(self):
        assert fmt_currency(1234.56) == "$1,234.56"

    def test_zero(self):
        assert fmt_currency(0) == "$0.00"

    def test_negative(self):
        assert fmt_currency(-50.5) == "$-50.50"

    def test_nan(self):
        assert fmt_currency(float("nan")) == ""

    def test_none(self):
        assert fmt_currency(None) == ""


class TestFmtPct:
    def test_positive(self):
        assert fmt_pct(45.67) == "45.7%"

    def test_zero(self):
        assert fmt_pct(0) == "0.0%"

    def test_nan(self):
        assert fmt_pct(float("nan")) == ""


class TestFmtInt:
    def test_positive(self):
        assert fmt_int(12345) == "12,345"

    def test_zero(self):
        assert fmt_int(0) == "0"

    def test_nan(self):
        assert fmt_int(float("nan")) == ""

    def test_float_truncated(self):
        assert fmt_int(99.7) == "99"


class TestFcHtml:
    def test_positive(self):
        assert fc(1234.56) == "$1,234.56"

    def test_negative(self):
        assert fc(-50.5) == "-$50.50"

    def test_zero(self):
        assert fc(0) == "$0.00"

    def test_none(self):
        assert fc(None) == "$0.00"

    def test_nan(self):
        assert fc(float("nan")) == "$0.00"


class TestFpHtml:
    def test_value(self):
        assert fp(45.67) == "45.7%"

    def test_none(self):
        assert fp(None) == "0.0%"

    def test_nan(self):
        assert fp(float("nan")) == "0.0%"


class TestFiHtml:
    def test_value(self):
        assert fi(12345) == "12,345"

    def test_none(self):
        assert fi(None) == "0"


class TestToPy:
    def test_regular_float(self):
        assert to_py(3.14) == 3.14

    def test_nan_becomes_zero(self):
        assert to_py(float("nan")) == 0

    def test_inf_becomes_zero(self):
        assert to_py(float("inf")) == 0

    def test_int(self):
        assert to_py(42) == 42


class TestJslist:
    def test_basic(self):
        result = jslist([1.0, 2.5, 3.123456])
        assert result == "[1.0, 2.5, 3.12]"

    def test_empty(self):
        assert jslist([]) == "[]"


class TestEsc:
    def test_ampersand(self):
        assert esc("A & B") == "A &amp; B"

    def test_angle_brackets(self):
        assert esc("<script>") == "&lt;script&gt;"

    def test_quotes(self):
        assert esc('He said "hi"') == "He said &quot;hi&quot;"

    def test_single_quote(self):
        assert esc("it's") == "it&#39;s"

    def test_plain(self):
        assert esc("hello") == "hello"
