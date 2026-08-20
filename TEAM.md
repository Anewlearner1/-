# 投資團隊 — 五位大師分析師的兩輪辯論

在既有的台股資料管線上，加一層「投資團隊」：5 位投資大師人格各自分析、互相質詢，
最後以完全平權方式加總成一份結構化評分。**主理人是你本人**，AI 只出意見與評分，不代為拍板。

## 快速開始

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

python team.py --list-team              # 看五位分析師的完整背景卡
python team.py 2330.TW 2454.TW          # 開一場會
python team.py 2330.TW --period 3mo     # 拉長取樣區間
python team.py 2330.TW --no-cross-exam  # 只跑第一輪，成本減半
```

標的每次由你指定（支援任何 yfinance 代號，含 `^TWII` 大盤）。

## 團隊成員

| 分析師 | 學派 | 最在意 | 已知盲點 |
|---|---|---|---|
| 華倫・巴菲特 | 價值投資派 | 自由現金流、ROE、護城河、安全邊際 | 對高成長無盈餘題材反應偏慢 |
| 彼得・林區 | 成長股 / 由下而上 | PEG、營收與 EPS 成長、故事還能演多久 | 刻意忽略總經與系統性風險 |
| 喬治・索羅斯 | 宏觀 / 反身性 | 利率匯率、資金流向、共識敘事的錯誤 | 盤整期容易過度解讀雜訊 |
| 詹姆斯・西蒙斯 | 量化 / 統計套利 | 均值回歸機率、訊號勝率與樣本數 | 結構性轉折時歷史關係失效 |
| 納西姆・塔雷伯 | 黑天鵝 / 尾端風險 | 最大虧損、損益不對稱、流動性風險 | 系統性偏空，長多行情中拖累報酬 |

每張背景卡都含資歷、投資哲學、關鍵指標、忌諱清單、口吻風格與典型盲點，
定義在 `personas.py`，直接改那份 dict 就能微調人設。

**免責聲明**：這些人格是 AI 依公開投資哲學所做的模擬，不是本人的意見或授權背書，
也不構成投資建議。每份輸出都會附帶這段聲明。

## 討論流程

```
資料包（技術面 + 基本面 + 大盤寬度）
   ↓
Round 1  五人平行獨立發言（看不到彼此意見）
   ↓
Round 2  每人讀完其他四人的論點 → 具體質詢 → 可修正自己的立場
   ↓
完全平權加總 → 團隊共識 / 分歧度 / 決策建議書 JSON
```

一場會 = 10 次 API 呼叫（5 人 × 2 輪，同一人的第二輪沿用第一輪的快取前綴）。
`--no-cross-exam` 降到 5 次。模型為 `claude-opus-5`，可用 `TEAM_EFFORT`
環境變數調 `low`/`medium`/`high`/`xhigh`/`max`（預設 `high`）。

## 評分加總規則

完全平權：五人一票等重，**不**做任何角色偏重。

- 立場換算：`BUY = +1`、`HOLD = 0`、`SELL = -1`
- 個人分數 = 立場 × (信心度 / 10)，落在 `-1.0 ~ +1.0`
- 團隊分數 = 五人分數的算術平均
- 團隊立場：`≥ +0.25` → BUY，`≤ -0.25` → SELL，其餘 → HOLD
- 分歧度 = 五人分數的標準差；只要有人買、有人賣，或分歧度 ≥ 0.5，
  就標記 `needs_manual_review`，提醒你逐條讀完雙方論點再決定

## 輸出

每場會產出一份 JSON 到 `reports/team/team_meeting_<時間戳>.json`：

```jsonc
{
  "meeting_time": "...",
  "moderator": "使用者本人（AI 不代為拍板）",
  "voting_rule": "完全平權：五人一人一票，以信心度加權後取平均",
  "consensus": {
    "2330.TW": {
      "team_stance": "BUY",
      "team_score": 0.32,
      "votes": { "BUY": 3, "HOLD": 1, "SELL": 1 },
      "dispersion": 0.598,
      "is_split": true,
      "needs_manual_review": true,
      "avg_conviction": 7.0,
      "target_price_range": { "low": 1100, "high": 1350, "median": 1200 },
      "tightest_stop_loss": 1000,
      "positions": [ /* 每位分析師的立場、信心、論點、風險、停損、是否被說服 */ ]
    }
  },
  "round1": { /* 五人第一輪原始發言 */ },
  "round2": { /* 質詢內容與修正後評分 */ },
  "market_packet": { /* 當次餵給模型的完整原始資料，可用於回測 */ },
  "usage": { /* token 用量 */ },
  "disclaimer": "..."
}
```

`round1` / `round2` / `market_packet` 全部保留，方便日後回測「誰的判斷比較準」
以及「第二輪辯論有沒有讓結論變好」。

## 檔案

| 檔案 | 作用 |
|---|---|
| `personas.py` | 五張背景卡、system prompt 組裝、平權權重表 |
| `discussion.py` | 資料包組裝、兩輪辯論引擎、結構化 schema、平權加總 |
| `team.py` | 命令列進入點、決策建議書列印、JSON 存檔 |

沿用既有的 `fetcher.py`（抓價量與基本面）與 `analyzer.py`（技術指標），
不影響原本 `main.py` 的每小時大盤分析流程。
