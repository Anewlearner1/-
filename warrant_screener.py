#!/usr/bin/env python3
"""
權證篩選器 (Warrant Screener)

流程：
  1. 讀取權證資料 CSV
  2. 計算衍生欄位（價差比、價外幅度）
  3. 第一層：硬性門檻過濾（不符合直接剔除，並記錄剔除原因）
  4. 第二層：加分項評分（符合幾項就得幾分）
  5. 輸出排序後的候選清單（CSV），並在終端機顯示摘要

用法：
  python warrant_screener.py input.csv
  python warrant_screener.py input.csv -o result.csv --min-score 4
  python warrant_screener.py --demo            # 產生範例資料並直接測試
  python warrant_screener.py --template        # 輸出空白 CSV 範本

注意：本工具僅為輔助篩選，不構成投資建議。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 設定：所有門檻集中在這裡，方便調整
# ---------------------------------------------------------------------------


@dataclass
class HardFilter:
    """第一層：硬性門檻"""
    min_days_to_expiry: int = 90        # 剩餘天數下限
    min_leverage: float = 3.0           # 實質槓桿下限
    max_leverage: float = 6.0           # 實質槓桿上限
    min_otm_pct: float = 0.0            # 價外幅度下限 (%)，0 = 價平
    max_otm_pct: float = 15.0           # 價外幅度上限 (%)
    max_spread_pct: float = 1.0         # 委買委賣價差比上限 (%)
    min_avg_volume_5d: int = 100        # 權證近 5 日均量下限 (張)
    min_underlying_volume: int = 3000   # 標的股日均量下限 (張)
    # 發行商白名單；留空 list 代表不限制
    allowed_issuers: list[str] = field(default_factory=list)


@dataclass
class SoftScore:
    """第二層：加分項"""
    delta_low: float = 0.4              # Delta 理想區間
    delta_high: float = 0.6
    max_outstanding_pct: float = 50.0   # 流通在外比例上限 (%)，越低越好
    min_days_to_event: int = 14         # 距離催化事件至少幾天
    iv_hv_discount: float = 0.0         # IV 需低於 HV 至少幾個百分點
    max_score: int = 6                  # 滿分（含 IV 同標的最低那一項）


HARD = HardFilter()
SOFT = SoftScore()

# CSV 必要欄位
REQUIRED_COLS = [
    "warrant_code",        # 權證代號
    "underlying",          # 標的代號／名稱
    "issuer",              # 發行商
    "warrant_type",        # 認購 / 認售 (call / put)
    "strike",              # 履約價
    "underlying_price",    # 標的現價
    "days_to_expiry",      # 剩餘天數
    "leverage",            # 實質槓桿
    "bid",                 # 委買價
    "ask",                 # 委賣價
    "avg_volume_5d",       # 權證近 5 日均量
    "underlying_volume",   # 標的股日均量
    "iv",                  # 隱含波動率 (%)
    "delta",               # Delta (絕對值)
]

# 選填欄位
OPTIONAL_COLS = [
    "hv20",                # 標的近 20 日歷史波動率 (%)
    "outstanding_pct",     # 流通在外比例 (%)
    "days_to_event",       # 距離下一個催化事件日（天）
    "event_before_expiry", # 事件日是否在到期日之前 (1/0/True/False)
]


# ---------------------------------------------------------------------------
# 載入與前處理
# ---------------------------------------------------------------------------


def load_data(path: str | Path) -> pd.DataFrame:
    # 權證代號／標的代號一律當字串讀，否則 "030081" 會被當成數字而掉頭 0
    df = pd.read_csv(path, encoding="utf-8-sig",
                     dtype={"warrant_code": str, "underlying": str, "issuer": str})
    df.columns = [c.strip() for c in df.columns]
    for c in ("warrant_code", "underlying"):
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV 缺少必要欄位：{missing}\n"
            f"可用 --template 產生範本。"
        )

    for col in OPTIONAL_COLS:
        if col not in df.columns:
            df[col] = np.nan

    numeric_cols = [
        "strike", "underlying_price", "days_to_expiry", "leverage", "bid",
        "ask", "avg_volume_5d", "underlying_volume", "iv", "delta",
        "hv20", "outstanding_pct", "days_to_event",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["delta"] = df["delta"].abs()
    df["event_before_expiry"] = (
        df["event_before_expiry"]
        .map(lambda v: True if str(v).strip().lower() in ("1", "true", "y", "yes", "是")
             else (False if str(v).strip().lower() in ("0", "false", "n", "no", "否")
                   else np.nan))
    )
    return df


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    mid = (df["bid"] + df["ask"]) / 2
    df["spread_pct"] = np.where(mid > 0, (df["ask"] - df["bid"]) / mid * 100, np.nan)

    is_call = df["warrant_type"].astype(str).str.contains("購|call", case=False, regex=True)
    # 認購：履約價高於現價為價外；認售：履約價低於現價為價外
    otm_call = (df["strike"] - df["underlying_price"]) / df["underlying_price"] * 100
    otm_put = (df["underlying_price"] - df["strike"]) / df["underlying_price"] * 100
    df["otm_pct"] = np.where(is_call, otm_call, otm_put)

    df["iv_hv_gap"] = df["iv"] - df["hv20"]  # 負值 = IV 低於 HV（折價）
    return df


# ---------------------------------------------------------------------------
# 第一層：硬性門檻
# ---------------------------------------------------------------------------


def apply_hard_filters(df: pd.DataFrame, cfg: HardFilter) -> pd.DataFrame:
    df = df.copy()
    reasons: list[list[str]] = [[] for _ in range(len(df))]

    def check(mask: pd.Series, msg: str) -> None:
        for i, ok in enumerate(mask.fillna(False).to_numpy()):
            if not ok:
                reasons[i].append(msg)

    check(df["days_to_expiry"] >= cfg.min_days_to_expiry,
          f"剩餘天數<{cfg.min_days_to_expiry}")
    check(df["leverage"].between(cfg.min_leverage, cfg.max_leverage),
          f"槓桿不在{cfg.min_leverage}-{cfg.max_leverage}")
    check(df["otm_pct"].between(cfg.min_otm_pct, cfg.max_otm_pct),
          f"價外不在{cfg.min_otm_pct}-{cfg.max_otm_pct}%")
    check(df["spread_pct"] <= cfg.max_spread_pct,
          f"價差比>{cfg.max_spread_pct}%")
    check(df["avg_volume_5d"] >= cfg.min_avg_volume_5d,
          f"權證5日均量<{cfg.min_avg_volume_5d}")
    check(df["underlying_volume"] >= cfg.min_underlying_volume,
          f"標的均量<{cfg.min_underlying_volume}")

    if cfg.allowed_issuers:
        check(df["issuer"].isin(cfg.allowed_issuers), "發行商不在白名單")

    df["reject_reasons"] = ["；".join(r) for r in reasons]
    df["passed_hard"] = [len(r) == 0 for r in reasons]
    return df


# ---------------------------------------------------------------------------
# 第二層：加分項評分
# ---------------------------------------------------------------------------


# 每個加分項需要哪一欄才算得出來。欄位整欄都是空的 -> 這一項誰也拿不到分，
# 不該算進滿分裡，否則「2 分」看起來很差，其實已經是當下的滿分。
SCORE_INPUTS = {
    "同標的IV最低": "iv",
    "IV低於HV": "hv20",
    "Delta 0.4-0.6": "delta",
    "流通在外低": "outstanding_pct",
    "事件距離足夠": "days_to_event",
    "涵蓋事件日": "event_before_expiry",
}


def scorable_items(df: pd.DataFrame) -> dict[str, bool]:
    """哪些加分項的資料是有的（整欄皆空就是拿不到）。"""
    return {name: bool(col in df.columns and df[col].notna().any())
            for name, col in SCORE_INPUTS.items()}


def apply_scoring(df: pd.DataFrame, cfg: SoftScore) -> pd.DataFrame:
    df = df.copy()
    df["score"] = 0
    df["score_detail"] = ""
    df["score_max"] = sum(scorable_items(df).values())

    passed = df["passed_hard"]
    if not passed.any():
        return df

    # 1) 同標的中 IV 最低（僅在通過硬性門檻的權證間比較）
    iv_min_by_underlying = df[passed].groupby("underlying")["iv"].transform("min")
    is_lowest_iv = pd.Series(False, index=df.index)
    is_lowest_iv.loc[passed] = df.loc[passed, "iv"] <= iv_min_by_underlying * 1.02  # 容許 2% 誤差

    # 2) IV 相對 HV 折價
    iv_discount = (df["hv20"] - df["iv"]) > cfg.iv_hv_discount

    # 3) Delta 在理想區間
    delta_ok = df["delta"].between(cfg.delta_low, cfg.delta_high)

    # 4) 流通在外比例低
    out_ok = df["outstanding_pct"] <= cfg.max_outstanding_pct

    # 5) 距離催化事件 >= N 天
    event_gap_ok = df["days_to_event"] >= cfg.min_days_to_event

    # 6) 存續期間涵蓋事件日
    covers_event = df["event_before_expiry"] == True  # noqa: E712

    items = {
        "同標的IV最低": is_lowest_iv,
        "IV低於HV": iv_discount,
        "Delta 0.4-0.6": delta_ok,
        "流通在外低": out_ok,
        "事件距離足夠": event_gap_ok,
        "涵蓋事件日": covers_event,
    }

    score = pd.Series(0, index=df.index)
    details = pd.Series("", index=df.index)
    for name, mask in items.items():
        m = mask.fillna(False)
        score += m.astype(int)
        details = details.where(~m, details + name + "、")

    df["score"] = np.where(passed, score, 0)
    df["score_detail"] = np.where(passed, details.str.rstrip("、"), "")
    return df


# ---------------------------------------------------------------------------
# 主流程與輸出
# ---------------------------------------------------------------------------

DISPLAY_COLS = [
    "warrant_code", "underlying", "issuer", "warrant_type", "days_to_expiry",
    "leverage", "otm_pct", "spread_pct", "iv", "hv20", "delta", "score",
    "score_max", "score_detail",
]


def screen(df: pd.DataFrame, hard: HardFilter = HARD, soft: SoftScore = SOFT,
           min_score: int = 4) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """回傳 (候選清單, 通過但分數不足, 被剔除)"""
    df = add_derived(df)
    df = apply_hard_filters(df, hard)
    df = apply_scoring(df, soft)

    passed = df[df["passed_hard"]].copy()
    rejected = df[~df["passed_hard"]].copy()

    passed = passed.sort_values(
        ["score", "spread_pct", "iv"], ascending=[False, True, True]
    )
    candidates = passed[passed["score"] >= min_score]
    below = passed[passed["score"] < min_score]
    return candidates, below, rejected


def print_summary(total: int, cand: pd.DataFrame, below: pd.DataFrame,
                  rej: pd.DataFrame, min_score: int,
                  avail: dict[str, bool] | None = None) -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.unicode.east_asian_width", True)

    print("=" * 70)
    print(f"共 {total} 檔｜通過硬性門檻 {len(cand) + len(below)} 檔｜"
          f"剔除 {len(rej)} 檔｜候選（>= {min_score} 分）{len(cand)} 檔")
    if avail is not None:
        usable = sum(avail.values())
        missing = [k for k, v in avail.items() if not v]
        print(f"實際滿分 {usable}/{len(avail)} 分" +
              (f"｜缺資料而無法得分：{'、'.join(missing)}" if missing else ""))
        if missing and min_score > usable:
            print(f"注意：--min-score {min_score} 已經高於實際滿分 {usable}，"
                  f"不可能有候選。")
    print("=" * 70)

    if len(cand):
        print("\n【候選清單】")
        print(cand[DISPLAY_COLS].round(2).to_string(index=False))
    else:
        print("\n【候選清單】無符合條件的權證。可調整門檻或降低 --min-score。")

    if len(below):
        print(f"\n【通過硬性門檻但分數不足 {min_score}】")
        print(below[DISPLAY_COLS].round(2).head(10).to_string(index=False))

    if len(rej):
        print("\n【剔除原因統計】")
        counts: dict[str, int] = {}
        for text in rej["reject_reasons"]:
            for r in str(text).split("；"):
                if r:
                    counts[r] = counts.get(r, 0) + 1
        for r, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {r}: {n}")
    print()


# ---------------------------------------------------------------------------
# 範例資料與範本
# ---------------------------------------------------------------------------


def make_template(path: str) -> None:
    pd.DataFrame(columns=REQUIRED_COLS + OPTIONAL_COLS).to_csv(
        path, index=False, encoding="utf-8-sig")
    print(f"已輸出範本：{path}")


def make_demo(path: str) -> None:
    rng = np.random.default_rng(42)
    issuers = ["元大", "凱基", "富邦", "國泰", "群益"]
    underlyings = {"2330": 1000.0, "2317": 210.0, "3231": 120.0, "2454": 1300.0}
    rows = []
    n = 0
    for und, px in underlyings.items():
        for _ in range(12):
            n += 1
            is_call = rng.random() < 0.85
            otm = rng.uniform(-5, 25)
            strike = px * (1 + otm / 100) if is_call else px * (1 - otm / 100)
            days = int(rng.integers(30, 240))
            delta = float(np.clip(0.55 - otm / 100 * 1.6 + rng.normal(0, 0.05), 0.05, 0.9))
            lev = float(np.clip(delta * rng.uniform(6, 14), 1.5, 15))
            ask = round(float(rng.uniform(0.6, 4.0)), 2)
            spread_ratio = float(rng.uniform(0.002, 0.03))
            bid = round(ask * (1 - spread_ratio), 2)
            hv = float(rng.uniform(25, 55))
            iv = float(hv + rng.uniform(-12, 15))
            rows.append({
                "warrant_code": f"{70000 + n}",
                "underlying": und,
                "issuer": rng.choice(issuers),
                "warrant_type": "認購" if is_call else "認售",
                "strike": round(strike, 1),
                "underlying_price": px,
                "days_to_expiry": days,
                "leverage": round(lev, 2),
                "bid": bid,
                "ask": ask,
                "avg_volume_5d": int(rng.integers(20, 1500)),
                "underlying_volume": int(rng.integers(1500, 40000)),
                "iv": round(iv, 1),
                "delta": round(delta, 2),
                "hv20": round(hv, 1),
                "outstanding_pct": round(float(rng.uniform(5, 95)), 1),
                "days_to_event": int(rng.integers(3, 60)),
                "event_before_expiry": int(rng.random() < 0.8),
            })
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"已產生範例資料：{path}（{len(rows)} 檔，純屬虛構，僅供測試）")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="權證篩選器")
    p.add_argument("input", nargs="?", help="輸入 CSV 路徑")
    p.add_argument("-o", "--output", default="warrant_candidates.csv",
                   help="候選清單輸出路徑")
    p.add_argument("--min-score", type=int, default=4, help="候選最低分數（預設 4）")
    p.add_argument("--min-days", type=int, default=HARD.min_days_to_expiry)
    p.add_argument("--min-lev", type=float, default=HARD.min_leverage)
    p.add_argument("--max-lev", type=float, default=HARD.max_leverage)
    p.add_argument("--max-otm", type=float, default=HARD.max_otm_pct)
    p.add_argument("--max-spread", type=float, default=HARD.max_spread_pct)
    p.add_argument("--min-vol", type=int, default=HARD.min_avg_volume_5d)
    p.add_argument("--min-und-vol", type=int, default=HARD.min_underlying_volume)
    p.add_argument("--issuers", nargs="*", default=[],
                   help="發行商白名單，例如 --issuers 元大 凱基 富邦")
    p.add_argument("--save-all", action="store_true",
                   help="另存完整結果（含被剔除者與原因）")
    p.add_argument("--demo", action="store_true", help="產生範例資料並測試")
    p.add_argument("--template", action="store_true", help="輸出空白 CSV 範本")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.template:
        make_template("warrant_template.csv")
        return 0

    if args.demo:
        make_demo("demo_warrants.csv")
        args.input = "demo_warrants.csv"

    if not args.input:
        print("請提供輸入 CSV，或使用 --demo / --template。", file=sys.stderr)
        return 1

    hard = HardFilter(
        min_days_to_expiry=args.min_days,
        min_leverage=args.min_lev,
        max_leverage=args.max_lev,
        max_otm_pct=args.max_otm,
        max_spread_pct=args.max_spread,
        min_avg_volume_5d=args.min_vol,
        min_underlying_volume=args.min_und_vol,
        allowed_issuers=args.issuers,
    )

    df = load_data(args.input)
    avail = scorable_items(df)
    cand, below, rej = screen(df, hard, SOFT, args.min_score)
    print_summary(len(df), cand, below, rej, args.min_score, avail)

    cand.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"候選清單已輸出：{args.output}")

    if args.save_all:
        full = pd.concat([cand, below, rej])
        full_path = Path(args.output).with_name("warrant_full_result.csv")
        full.to_csv(full_path, index=False, encoding="utf-8-sig")
        print(f"完整結果已輸出：{full_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
