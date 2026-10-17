from __future__ import annotations

import pytest

from src.sources.ats import html_to_text, parse_workday_url, workday_posted_days


@pytest.mark.parametrize("text,days", [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 5 Days Ago", 5),
                                       ("Posted 30+ Days Ago", 31), ("", None)])
def test_workday_posted_days(text, days):
    assert workday_posted_days(text) == days


def test_parse_workday_url():
    assert parse_workday_url("https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers") == \
        ("mastercard.wd1.myworkdayjobs.com", "mastercard", "CorporateCareers")


def test_html_to_text_unescapes():
    assert html_to_text("&lt;p&gt;Hello&lt;/p&gt;<b>world</b>").replace("\n", " ").split() == ["Hello", "world"]


def test_jobspy_parallel_first_import_is_safe():
    """Regression: concurrent first imports of jobspy deadlocked in CI."""
    from concurrent.futures import ThreadPoolExecutor
    from src.sources.jobspy_source import load_jobspy
    with ThreadPoolExecutor(6) as pool:
        fns = list(pool.map(lambda _: load_jobspy(), range(6)))
    assert all(callable(f) for f in fns)
