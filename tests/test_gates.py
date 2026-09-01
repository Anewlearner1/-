"""
分層觸發的規格 —— token 要花在有爭議的地方，那也正是資訊最多的地方。

兩道閘門：
    資料健全   資料包是空的就別開會（先前整場會議產出「資料不足」×5）
    分歧門檻   R1 若毫無分歧，R2 就是五份「維持原判」，純浪費
"""
import gates


# --------------------------------------------------------------------------
# 資料健全：沒有資料就不要浪費一場會議
# --------------------------------------------------------------------------

def test_packet_with_technicals_is_healthy():
    ok, why = gates.packet_is_usable({"symbols": ["A"], "technicals": {"A": {"last_price": 100}}})
    assert ok is True and why == ""


def test_packet_with_all_symbols_failed_is_rejected():
    ok, why = gates.packet_is_usable(
        {"symbols": ["A", "B"], "technicals": {"A": {"error": "資料不足"}, "B": {"error": "資料不足"}}})
    assert ok is False
    assert "沒有任何標的" in why


def test_packet_with_one_good_symbol_still_runs():
    """部分標的有資料就該開會，只是壞的那檔會被分析師標記。"""
    ok, _ = gates.packet_is_usable(
        {"symbols": ["A", "B"], "technicals": {"A": {"last_price": 100}, "B": {"error": "x"}}})
    assert ok is True


def test_missing_price_counts_as_unusable():
    """有 technicals 但沒有現價 —— 算不出賠率，等於沒資料。"""
    ok, _ = gates.packet_is_usable({"symbols": ["A"], "technicals": {"A": {"rsi": 60}}})
    assert ok is False


# --------------------------------------------------------------------------
# 分歧門檻：全員一致就不必再辯一輪
# --------------------------------------------------------------------------

def _r1(stances, ps=None):
    ps = ps or [0.5] * len(stances)
    return {
        f"a{i}": {"calls": [{"symbol": "X", "stance": s, "p_target": p,
                             "target_price": 130, "stop_loss": 90}]}
        for i, (s, p) in enumerate(zip(stances, ps))
    }


def test_unanimous_stance_with_similar_probabilities_skips_round2():
    need, why = gates.needs_cross_exam(_r1(["BUY"] * 5, [0.6, 0.62, 0.58, 0.6, 0.61]), ["X"])
    assert need is False
    assert "一致" in why


def test_split_stance_requires_round2():
    need, why = gates.needs_cross_exam(_r1(["BUY", "BUY", "SELL", "HOLD", "BUY"]), ["X"])
    assert need is True
    assert "分歧" in why


def test_same_stance_but_wildly_different_probabilities_requires_round2():
    """立場一致但機率從 0.3 到 0.9 —— 他們其實沒有共識，只是碰巧同向。"""
    need, _ = gates.needs_cross_exam(_r1(["BUY"] * 5, [0.3, 0.9, 0.35, 0.85, 0.5]), ["X"])
    assert need is True


def test_any_contested_symbol_triggers_round2_for_everyone():
    """多檔標的時，只要有一檔有爭議就跑第二輪 —— 質詢無法只對單一標的做。"""
    r1 = {
        "a0": {"calls": [{"symbol": "X", "stance": "BUY", "p_target": 0.6},
                         {"symbol": "Y", "stance": "BUY", "p_target": 0.6}]},
        "a1": {"calls": [{"symbol": "X", "stance": "BUY", "p_target": 0.6},
                         {"symbol": "Y", "stance": "SELL", "p_target": 0.6}]},
    }
    assert gates.needs_cross_exam(r1, ["X", "Y"])[0] is True


def test_failed_analysts_do_not_fake_consensus():
    """五人裡三人掛掉，剩兩人同向不算共識 —— 樣本太小要辯論。"""
    r1 = {"a0": {"calls": [{"symbol": "X", "stance": "BUY", "p_target": 0.6}]},
          "a1": {"calls": [{"symbol": "X", "stance": "BUY", "p_target": 0.6}]},
          "a2": {"error": "timeout"}, "a3": {"error": "timeout"}, "a4": {"error": "timeout"}}
    need, why = gates.needs_cross_exam(r1, ["X"])
    assert need is True
    assert "人數" in why


def test_no_usable_round1_does_not_request_round2():
    """全員失敗時第二輪也救不了，不要再燒一輪 token。"""
    assert gates.needs_cross_exam({"a0": {"error": "x"}}, ["X"])[0] is False
