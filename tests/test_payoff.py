"""
賠率結構的規格 —— 報酬來自「勝率 × 賠率 × 部位」，不是方向本身。

這裡的每個數字都由 Python 算，不由 LLM 算：LLM 負責估機率與價位，
算術不該交給會算錯的東西。
"""
import pytest
import payoff


# --------------------------------------------------------------------------
# R_target：這筆交易賭贏能拿幾倍風險
# --------------------------------------------------------------------------

def test_long_r_target():
    """進場100 停損90（風險10）目標130（報酬30）→ 3:1。"""
    assert payoff.r_target("BUY", 100, 90, 130) == pytest.approx(3.0)


def test_short_r_target_is_mirrored():
    assert payoff.r_target("SELL", 100, 110, 70) == pytest.approx(3.0)


@pytest.mark.parametrize("stance,entry,stop,target", [
    ("BUY", 100, None, 130),     # 沒停損 → 風險未定義
    ("BUY", 100, 90, None),      # 沒目標 → 報酬未定義
    ("BUY", 100, 100, 130),      # 零風險 → 不可除
    ("BUY", 100, 90, 95),        # 目標比進場低：BUY 卻預期下跌，矛盾
    ("SELL", 100, 110, 130),     # 目標比進場高：SELL 卻預期上漲，矛盾
    ("HOLD", 100, 90, 130),      # 不預測就沒有賠率
])
def test_r_target_is_none_when_undefined(stance, entry, stop, target):
    """算不出來就誠實回 None，絕不編一個數字 —— 假精確比沒有更危險。"""
    assert payoff.r_target(stance, entry, stop, target) is None


# --------------------------------------------------------------------------
# Expected R：期望值本身
# --------------------------------------------------------------------------

def test_expected_r_is_probability_weighted():
    """p=0.5、賠率3:1 → 0.5×3 − 0.5×1 = +1.0R。"""
    assert payoff.expected_r(0.5, 3.0) == pytest.approx(1.0)


def test_expected_r_can_be_negative_despite_good_payoff():
    """賠率2:1 但只有兩成機率 → 0.2×2 − 0.8×1 = -0.4R，是賠錢的賭注。"""
    assert payoff.expected_r(0.2, 2.0) == pytest.approx(-0.4)


def test_expected_r_needs_both_inputs():
    assert payoff.expected_r(None, 3.0) is None
    assert payoff.expected_r(0.5, None) is None


@pytest.mark.parametrize("p", [-0.5, 1.5])
def test_probability_out_of_range_is_clamped(p):
    """模型偶爾會給 1.2 這種機率；夾限而不是讓它污染期望值。"""
    assert payoff.expected_r(p, 2.0) == payoff.expected_r(min(max(p, 0), 1), 2.0)


# --------------------------------------------------------------------------
# 不對稱性門檻：只有賠率夠好且期望值為正才算可執行
# --------------------------------------------------------------------------

def test_good_payoff_and_positive_ev_is_actionable():
    a = payoff.assess({"stance": "BUY", "entry": 100, "stop": 90,
                       "target": 130, "p_target": 0.5})
    assert a["actionable"] is True
    assert a["expected_r"] == pytest.approx(1.0)


def test_thin_payoff_is_rejected_even_with_high_probability():
    """賠率1.5:1 未達門檻 —— 高勝率低賠率是最常見的破產方式。"""
    a = payoff.assess({"stance": "BUY", "entry": 100, "stop": 90,
                       "target": 115, "p_target": 0.9})
    assert a["r_target"] == pytest.approx(1.5)
    assert a["expected_r"] > 0, "期望值其實是正的"
    assert a["actionable"] is False, "但賠率沒過門檻，仍不該執行"
    assert "賠率" in a["reason"]


def test_negative_ev_is_rejected_even_with_great_payoff():
    a = payoff.assess({"stance": "BUY", "entry": 100, "stop": 90,
                       "target": 200, "p_target": 0.05})
    assert a["r_target"] == pytest.approx(10.0)
    assert a["actionable"] is False
    assert "期望值" in a["reason"]


def test_hold_is_not_actionable_and_scores_zero():
    a = payoff.assess({"stance": "HOLD", "entry": 100, "stop": None,
                       "target": None, "p_target": None})
    assert a["actionable"] is False
    assert a["signed_expected_r"] == 0.0, "不持有部位，對多方視角的貢獻為零"


# --------------------------------------------------------------------------
# 有號期望值：把「這筆交易多好」轉成「站在多方，期望值多少」
# --------------------------------------------------------------------------

def test_sell_flips_sign_for_long_perspective():
    """關鍵陷阱：BUY 的 +2R 和 SELL 的 +2R 是相反立場，不可以直接平均。"""
    buy = payoff.assess({"stance": "BUY", "entry": 100, "stop": 90,
                         "target": 130, "p_target": 0.5})
    sell = payoff.assess({"stance": "SELL", "entry": 100, "stop": 110,
                          "target": 70, "p_target": 0.5})
    assert buy["signed_expected_r"] == pytest.approx(1.0)
    assert sell["signed_expected_r"] == pytest.approx(-1.0)
    assert (buy["signed_expected_r"] + sell["signed_expected_r"]) == pytest.approx(0.0)


def test_missing_numbers_give_zero_signed_score_not_a_guess():
    """沒給停損就算不出期望值 —— 記為 0 並說明，不可以猜。"""
    a = payoff.assess({"stance": "BUY", "entry": 100, "stop": None,
                       "target": 130, "p_target": 0.7})
    assert a["signed_expected_r"] == 0.0
    assert a["expected_r"] is None
    assert "停損" in a["reason"]


# --------------------------------------------------------------------------
# Brier 分數：機率估得準不準（越低越好，0.25 等於瞎猜）
# --------------------------------------------------------------------------

def test_brier_rewards_confident_and_correct():
    assert payoff.brier_score([(0.9, True), (0.9, True)]) == pytest.approx(0.01)


def test_brier_punishes_confident_and_wrong():
    assert payoff.brier_score([(0.9, False)]) == pytest.approx(0.81)


def test_brier_of_coin_flip_is_quarter():
    assert payoff.brier_score([(0.5, True), (0.5, False)]) == pytest.approx(0.25)


def test_brier_needs_data():
    assert payoff.brier_score([]) is None
