#!/usr/bin/env python3
"""
資料來源層 (data_sources.py)

設計原則：
  - 不憑記憶寫死任何 API 端點。證交所 OpenAPI 的端點在「執行期」
    直接讀官方 swagger.json 自動探索，端點改版時不會靜默出錯。
  - 權證本身的資料（IV、實質槓桿、流通在外比例）沒有穩定的公開 API，
    所以用「欄位對應表 (column map)」吃你從券商/權證網站匯出的檔案。
  - 所有網路請求都有快取、逾時、錯誤訊息，失敗時明確告訴你哪一步壞了。

三個部分：
  1. TwseOpenApi     ：讀 swagger.json，依關鍵字找端點並下載（標的股行情/成交量）
  2. compute_hv      ：由標的日收盤價序列計算歷史波動率
  3. WarrantAdapter  ：把任意來源的權證表，依欄位對應轉成標準格式

用法：
  python data_sources.py discover 收盤          # 列出名稱含「收盤」的端點
  python data_sources.py discover 權證          # 看官方 OpenAPI 有沒有權證相關端點
  python data_sources.py build raw.csv --map mapping.json -o warrants.csv
  python data_sources.py mapping-template       # 產生欄位對應範本
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:  # requests 只有連網時才需要
    requests = None

CACHE_DIR = Path(".cache_warrant")
TWSE_BASE = "https://openapi.twse.com.tw/v1"
TPEX_BASE = "https://www.tpex.org.tw/openapi/v1"
UA = {"User-Agent": "Mozilla/5.0 (warrant-screener; personal research use)"}


# ---------------------------------------------------------------------------
# 網路與快取
# ---------------------------------------------------------------------------


class DataSourceError(RuntimeError):
    pass


def _http_get_json(url: str, timeout: int = 20, retries: int = 2) -> Any:
    if requests is None:
        raise DataSourceError("需要 requests：pip install requests")
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + attempt)
    raise DataSourceError(f"下載失敗：{url}\n原因：{last}")


def _cached_json(url: str, ttl_hours: float = 6.0) -> Any:
    CACHE_DIR.mkdir(exist_ok=True)
    key = "".join(c if c.isalnum() else "_" for c in url)[-120:]
    f = CACHE_DIR / f"{key}.json"
    if f.exists() and (time.time() - f.stat().st_mtime) < ttl_hours * 3600:
        return json.loads(f.read_text(encoding="utf-8"))
    data = _http_get_json(url)
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# 1. 證交所 / 櫃買 OpenAPI：執行期自動探索
# ---------------------------------------------------------------------------


class TwseOpenApi:
    """讀 swagger.json，依關鍵字找端點，避免寫死網址。"""

    def __init__(self, base: str = TWSE_BASE) -> None:
        self.base = base.rstrip("/")
        self._spec: dict | None = None

    @property
    def spec(self) -> dict:
        if self._spec is None:
            self._spec = _cached_json(f"{self.base}/swagger.json", ttl_hours=24)
        return self._spec

    def search(self, keyword: str) -> list[tuple[str, str]]:
        """回傳 [(path, 說明)]，依路徑/摘要/說明中的關鍵字比對。"""
        out: list[tuple[str, str]] = []
        for path, methods in self.spec.get("paths", {}).items():
            get = methods.get("get") if isinstance(methods, dict) else None
            if not get:
                continue
            text = " ".join(str(get.get(k, "")) for k in ("summary", "description", "operationId"))
            if keyword.lower() in (path + " " + text).lower():
                out.append((path, (get.get("summary") or get.get("description") or "").strip()))
        return out

    def fetch(self, path: str, ttl_hours: float = 6.0) -> pd.DataFrame:
        data = _cached_json(f"{self.base}{path}", ttl_hours=ttl_hours)
        if isinstance(data, dict):  # 部分端點外層包了一層
            for v in data.values():
                if isinstance(v, list):
                    data = v
                    break
        return pd.DataFrame(data)

    def fetch_by_keyword(self, keyword: str, pick: int = 0) -> pd.DataFrame:
        hits = self.search(keyword)
        if not hits:
            raise DataSourceError(
                f"官方 OpenAPI 找不到含「{keyword}」的端點。\n"
                f"可用 `python data_sources.py discover <關鍵字>` 自行查詢。"
            )
        path = hits[pick][0]
        print(f"[data] 使用端點 {path}  ({hits[pick][1]})", file=sys.stderr)
        return self.fetch(path)


def underlying_snapshot() -> pd.DataFrame:
    """
    取得全市場「最新一日」個股成交資料，回傳標準欄位：
      underlying, close, volume_shares
    端點與欄位名在執行期依實際回傳判斷，失敗會明確報錯。
    """
    api = TwseOpenApi()
    df = api.fetch_by_keyword("收盤")  # 例如「上市個股日收盤價及月平均價」類端點
    code_col = _find_col(df, ["Code", "證券代號", "股票代號"])
    close_col = _find_col(df, ["ClosingPrice", "收盤價"])
    vol_col = _find_col(df, ["TradeVolume", "成交股數", "成交量"], required=False)
    out = pd.DataFrame({
        "underlying": df[code_col].astype(str).str.strip(),
        "close": pd.to_numeric(df[close_col].astype(str).str.replace(",", ""), errors="coerce"),
    })
    if vol_col:
        out["volume_shares"] = pd.to_numeric(
            df[vol_col].astype(str).str.replace(",", ""), errors="coerce")
    return out


def _find_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise DataSourceError(
            f"找不到欄位，嘗試過 {candidates}；實際欄位為 {list(df.columns)}。\n"
            f"請依實際欄位修改 _find_col 的候選清單。"
        )
    return None


# ---------------------------------------------------------------------------
# 2. 歷史波動率
# ---------------------------------------------------------------------------


def compute_hv(close: pd.Series, window: int = 20, trading_days: int = 252) -> float:
    """
    由日收盤價序列（時間由舊到新）計算年化歷史波動率，回傳百分比。
    使用對數報酬的樣本標準差 × sqrt(252)。資料不足時回傳 NaN。
    """
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < window + 1:
        return float("nan")
    ret = np.log(close / close.shift(1)).dropna().iloc[-window:]
    return float(ret.std(ddof=1) * np.sqrt(trading_days) * 100)


def hv_from_price_csv(path: str, window: int = 20) -> pd.DataFrame:
    """
    從你自己累積的日價格 CSV 計算各標的 HV。
    需要欄位：date, underlying, close。
    （官方 OpenAPI 多數只給最新一期，歷史要自己累積，或用你現有的資料庫。）
    """
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"underlying": str})
    df["date"] = pd.to_datetime(df["date"])
    rows = []
    for und, g in df.sort_values("date").groupby("underlying"):
        rows.append({"underlying": und, "hv20": compute_hv(g["close"], window)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. 權證資料適配器：欄位對應
# ---------------------------------------------------------------------------

STANDARD_COLS = [
    "warrant_code", "underlying", "issuer", "warrant_type", "strike",
    "underlying_price", "days_to_expiry", "leverage", "bid", "ask",
    "avg_volume_5d", "underlying_volume", "iv", "delta",
    "hv20", "outstanding_pct", "days_to_event", "event_before_expiry",
]

# 範本：左邊是「標準欄位」，右邊填你匯出檔裡「實際的欄位名稱」
MAPPING_TEMPLATE = {
    "_comment": "右邊填你匯出檔的欄位名稱；不存在的欄位填 null。可用 expiry_date 代替 days_to_expiry。",
    "warrant_code": "權證代號",
    "underlying": "標的代號",
    "issuer": "發行券商",
    "warrant_type": "權證類別",
    "strike": "履約價",
    "underlying_price": "標的股價",
    "days_to_expiry": "剩餘天數",
    "expiry_date": None,
    "leverage": "實質槓桿",
    "bid": "委買價",
    "ask": "委賣價",
    "avg_volume_5d": None,
    "underlying_volume": None,
    "iv": "隱含波動率",
    "delta": "Delta",
    "hv20": None,
    "outstanding_pct": "流通在外比例",
    "days_to_event": None,
    "event_before_expiry": None,
}


def _to_number(s: pd.Series) -> pd.Series:
    """處理 '12.5%'、'1,234'、'-' 等常見格式。"""
    return pd.to_numeric(
        s.astype(str).str.replace(",", "").str.replace("%", "").str.strip()
         .replace({"-": np.nan, "--": np.nan, "": np.nan, "nan": np.nan, "None": np.nan}),
        errors="coerce",
    )


def _normalize_type(s: pd.Series) -> pd.Series:
    def f(v: Any) -> str:
        t = str(v).lower()
        if "售" in t or "put" in t:
            return "認售"
        if "購" in t or "call" in t:
            return "認購"
        return str(v)
    return s.map(f)


class WarrantAdapter:
    """把任意來源的權證表，依 mapping 轉成標準欄位。"""

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = {k: v for k, v in mapping.items() if not k.startswith("_")}

    def transform(self, raw: pd.DataFrame, today: pd.Timestamp | None = None) -> pd.DataFrame:
        raw = raw.copy()
        raw.columns = [str(c).strip() for c in raw.columns]

        missing_src = [v for v in self.mapping.values() if v and v not in raw.columns]
        if missing_src:
            raise DataSourceError(
                f"對應表指定的欄位在檔案中找不到：{missing_src}\n"
                f"檔案實際欄位：{list(raw.columns)}"
            )

        out = pd.DataFrame(index=raw.index)
        for std, src in self.mapping.items():
            if std == "expiry_date":
                continue
            out[std] = raw[src] if src else np.nan

        # 剩餘天數：若沒有直接欄位，由到期日推算
        exp_src = self.mapping.get("expiry_date")
        if out["days_to_expiry"].isna().all() and exp_src:
            today = today or pd.Timestamp.today().normalize()
            exp = pd.to_datetime(raw[exp_src], errors="coerce")
            out["days_to_expiry"] = (exp - today).dt.days

        for col in ["strike", "underlying_price", "days_to_expiry", "leverage", "bid", "ask",
                    "avg_volume_5d", "underlying_volume", "iv", "delta", "hv20",
                    "outstanding_pct", "days_to_event"]:
            out[col] = _to_number(out[col])

        out["warrant_type"] = _normalize_type(out["warrant_type"])
        out["underlying"] = out["underlying"].astype(str).str.strip()
        out["warrant_code"] = out["warrant_code"].astype(str).str.strip()
        return out[STANDARD_COLS]


def enrich_with_underlying(df: pd.DataFrame, snap: pd.DataFrame | None = None,
                           hv: pd.DataFrame | None = None) -> pd.DataFrame:
    """用官方標的股資料補齊：標的現價、標的成交量；用 HV 表補 hv20。只補缺值，不覆蓋你原有資料。"""
    df = df.copy()
    if snap is not None:
        m = snap.set_index("underlying")
        if "close" in m:
            df["underlying_price"] = df["underlying_price"].fillna(df["underlying"].map(m["close"]))
        if "volume_shares" in m:
            vol_lots = df["underlying"].map(m["volume_shares"]) / 1000  # 股 -> 張
            df["underlying_volume"] = df["underlying_volume"].fillna(vol_lots)
    if hv is not None:
        df["hv20"] = df["hv20"].fillna(df["underlying"].map(hv.set_index("underlying")["hv20"]))
    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> int:
    api = TwseOpenApi(TPEX_BASE if args.tpex else TWSE_BASE)
    hits = api.search(args.keyword)
    if not hits:
        print(f"沒有找到含「{args.keyword}」的端點。")
        return 1
    for path, desc in hits:
        print(f"{path}\t{desc}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    raw = pd.read_csv(args.raw, encoding="utf-8-sig", dtype=str)
    mapping = json.loads(Path(args.map).read_text(encoding="utf-8"))
    df = WarrantAdapter(mapping).transform(raw)

    snap = hv = None
    if args.enrich:
        snap = underlying_snapshot()
    if args.hv_csv:
        hv = hv_from_price_csv(args.hv_csv)
    df = enrich_with_underlying(df, snap, hv)

    df.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"已輸出標準格式：{args.output}（{len(df)} 檔）")
    na = df.isna().mean()
    warn = na[na > 0.5]
    if len(warn):
        print("提醒：以下欄位超過一半是空值，對應的篩選條件/加分項將失效：")
        for c, r in warn.items():
            print(f"  {c}: {r:.0%} 空值")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="權證資料來源層")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="在官方 OpenAPI 搜尋端點")
    d.add_argument("keyword")
    d.add_argument("--tpex", action="store_true", help="改查櫃買中心")
    d.set_defaults(func=cmd_discover)

    b = sub.add_parser("build", help="把匯出檔轉成標準格式")
    b.add_argument("raw", help="券商/權證網站匯出的 CSV")
    b.add_argument("--map", required=True, help="欄位對應 JSON")
    b.add_argument("-o", "--output", default="warrants.csv")
    b.add_argument("--enrich", action="store_true", help="用證交所 OpenAPI 補標的股價與成交量")
    b.add_argument("--hv-csv", help="自己累積的日價 CSV (date,underlying,close)，用於計算 HV")
    b.set_defaults(func=cmd_build)

    t = sub.add_parser("mapping-template", help="輸出欄位對應範本")
    t.set_defaults(func=lambda a: (
        Path("mapping.json").write_text(
            json.dumps(MAPPING_TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8"),
        print("已輸出 mapping.json，請把右邊改成你檔案的實際欄位名稱。"), 0)[-1])

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except DataSourceError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
