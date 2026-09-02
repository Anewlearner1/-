from datetime import date

import pytest

from us_team.config import GoalConfig
from us_team.goal import cagr_to_monthly, describe_goal, evaluate_goal, milestones, required_cagr


CFG = GoalConfig(start_capital=30_000, target_capital=1_000_000, horizon_years=3.0)


def test_required_cagr_is_about_222_percent():
    cagr = required_cagr(30_000, 1_000_000, 3)
    assert cagr == pytest.approx(2.2185, abs=0.001)
    assert cagr_to_monthly(cagr) == pytest.approx(0.1024, abs=0.001)


def test_evaluate_goal_day_one_is_on_track():
    s = evaluate_goal(30_000, CFG, "2026-09-01", as_of=date(2026, 9, 1))
    assert s.status == "on_track"
    assert s.expected_equity_on_path == pytest.approx(30_000)
    assert s.progress_pct == 0
    assert s.remaining_years == pytest.approx(3.0)


def test_evaluate_goal_behind_after_one_year_flat():
    s = evaluate_goal(30_000, CFG, "2025-09-01", as_of=date(2026, 9, 1))
    assert s.status == "behind"
    # Two years left to 33x from flat → required CAGR jumps well above 222%
    assert s.cagr_required_now > s.cagr_required_at_start
    assert s.expected_equity_on_path == pytest.approx(30_000 * 3.2185, rel=0.01)


def test_evaluate_goal_ahead_and_reached():
    ahead = evaluate_goal(60_000, CFG, "2026-06-01", as_of=date(2026, 9, 1))
    assert ahead.status == "ahead"
    done = evaluate_goal(1_200_000, CFG, "2026-01-01", as_of=date(2026, 9, 1))
    assert done.status == "reached"
    assert done.progress_pct > 100


def test_evaluate_goal_expired():
    s = evaluate_goal(50_000, CFG, "2020-01-01", as_of=date(2026, 9, 1))
    assert s.status == "expired"
    assert "超過期限" in describe_goal(s)


def test_milestones_end_at_target():
    ms = milestones(CFG, "2026-09-01", step_months=6)
    assert ms[0]["equity_target"] == 30_000
    assert ms[-1]["month"] == 36
    assert ms[-1]["equity_target"] == pytest.approx(1_000_000, rel=0.001)
    assert all(ms[i]["equity_target"] < ms[i + 1]["equity_target"] for i in range(len(ms) - 1))
