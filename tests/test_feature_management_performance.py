"""Performance reports never replace a failed frozen acceptance gate."""

import pytest

from scripts.measure_feature_management_delivery import summarize, summarize_phases


def test_summary_reports_sample_count_median_and_nearest_rank_p95():
    assert summarize([1, 2, 3, 4, 100]) == {"samples": 5, "median": 3, "p95": 100, "min": 1, "max": 100}
    with pytest.raises(ValueError, match="empty"):
        summarize([])


def test_performance_aggregation_preserves_nested_phase_names_and_errors():
    reports = [
        {
            "name": "apply",
            "duration_ms": 4,
            "cpu_ms": 2,
            "rss_before": 10,
            "rss_after": 12,
            "threads_before": 2,
            "threads_after": 3,
            "io": {"read_count": 7},
            "error": None,
        },
        {
            "name": "probe",
            "duration_ms": 3,
            "cpu_ms": 1,
            "rss_before": 11,
            "rss_after": 11,
            "threads_before": 3,
            "threads_after": 3,
            "io": {"read_count": 3},
            "error": "TimeoutError",
        },
    ]
    result = summarize_phases(reports)
    assert result["apply"]["duration_ms"]["median"] == 4
    assert result["probe"]["duration_ms"]["median"] == 3
    assert result["probe"]["errors"] == {"TimeoutError": 1}
    assert result["apply"]["rss_delta"]["median"] == 2
    assert result["apply"]["io"]["read_count"]["median"] == 7
