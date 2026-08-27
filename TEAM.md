# 投資團隊 — 五位大師分析師的兩輪辯論

在既有的台股資料管線上，加一層「投資團隊」：5 位投資大師人格各自分析、互相質詢，
最後以完全平權方式加總成一份結構化評分。**主理人是你本人**，AI 只出意見與評分，不代為拍板。

## 快速開始

**不需要 API key** —— 走你本機已登入的 Claude Code CLI，吃現有的 Claude 訂閱額度。
完整安裝步驟見 [INSTALL.md](INSTALL.md)。

```bash
pip install -r requirements.txt
npm install -g @anthropic-ai/claude-code
claude login

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

一場會 = 10 次模型呼叫（5 人 × 2 輪），`--no-cross-exam` 降到 5 次。
實測單一標的、`TEAM_EFFORT=low` 約 2.5 分鐘跑完。

## 網路搜尋

五位分析師預設可以用 WebSearch 查資料包沒有的即時資訊——新聞、法說會、股東會、
公司公告、產業動態、總經事件。由 Anthropic 伺服器代打，**不吃本機出口網路**，
離線或被防火牆擋住的機器一樣能用（`--dump-packet` / `--packet` 拆分的資料抓取
是另一回事，那個仍需要本機能連到 yfinance / 證交所）。

- 每人用自己學派會在意的角度查，不強制一致的關鍵字
- 查到的每個具體主張都要附來源；查不到就誠實承認，不可編造網址
- 搜尋結果與資料包衝突時，分析師會在論點裡標註出來，而不是悄悄擇一使用
- 決策建議書與 JSON 輸出的每個立場都附 `web_sources`，但**未經人工查核**——
  引用的新聞或法說會內容仍可能過時、片面或被誤讀，使用前自己核實

```bash
python team.py 3037.TW --no-web-search   # 關閉，只根據資料包判斷（更快更省）
TEAM_WEB_SEARCH=0 python team.py 3037.TW  # 效果相同
```

## 執行後端

| `TEAM_BACKEND` | 認證方式 | 計費 |
|---|---|---|
| `claude_cli`（預設） | 本機 `claude login` 的訂閱登入 | 吃 Claude 訂閱額度，不需 API key |
| `api` | `ANTHROPIC_API_KEY` | Messages API 按 token 計費 |

兩個後端共用同一份 json_schema，輸出結構完全相同。
`TEAM_EFFORT` 可調 `low`/`medium`/`high`（預設）/`xhigh`/`max`；
`TEAM_MODEL` 可換模型（`claude_cli` 用 `opus`、`sonnet` 這類別名）。

輸出裡的 `usage.cost_usd` 在訂閱模式下是**額度換算的參考值**，不是實際扣款金額。

### 並行與登入穩定性

同一台機器上的多個 `claude` 行程共用同一份登入並協調 token 續期。五個行程同時
冷啟動、又剛好碰上續期時，舊版 CLI 會發生競態並撤銷儲存的登入
（`OAuth access token has been revoked`）。因此平行模式預設把五個行程
**錯開 2 秒**啟動（`TEAM_STAGGER` 可調），CLI 版本低於 2.1.211 時開會前會警告。
遇到登入被撤銷，用 `--serial` 一個一個跑可完全避開。

## 評分加總規則

完全平權：五人一票等重，**不**做任何角色偏重。

- 立場換算：`BUY = +1`、`HOLD = 0`、`SELL = -1`
- 個人分數 = 立場 × (信心度 / 10)，落在 `-1.0 ~ +1.0`
- 團隊分數 = 五人分數的算術平均
- 團隊立場：`≥ +0.25` → BUY，`≤ -0.25` → SELL，其餘 → HOLD
- 分歧度 = 五人分數的標準差；只要有人買、有人賣，分歧度 ≥ 0.5，
  或有人未計入，就標記 `needs_manual_review`
- 任何一位分析師沒被計入（失敗、漏答、立場無法辨識）都會列在 `excluded`
  並附原因。分母縮小一定會被揭露，不會靜默改變結論

## 輸出

每場會產出一份 JSON 到 `reports/team/team_meeting_<時間戳>.json`：

```jsonc
{
  "meeting_time": "...",
  "moderator": "使用者本人（AI 不代為拍板）",
  "voting_rule": "完全平權：五人一人一票，以信心度加權後取平均",
  "backend": "claude_cli（Claude 訂閱登入，免 API key，model=opus, effort=high）",
  "consensus": {
    "2330.TW": {
      "team_stance": "BUY",
      "team_score": 0.32,
      "votes": { "BUY": 3, "HOLD": 1, "SELL": 1 },
      "voter_count": 5,
      "expected_voters": 5,
      "excluded": [],          // 未計入者與原因，平權的分母絕不靜默縮小
      "data_warnings": [],     // 例如信心度超界被夾限
      "dispersion": 0.598,
      "is_split": true,
      "needs_manual_review": true,
      "avg_conviction": 7.0,
      "target_price_range": { "low": 1100, "high": 1350, "median": 1200 },
      "tightest_stop_loss": 1000,
      "positions": [ /* 立場、信心、論點、風險、停損、是否被說服、web_sources */ ]
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
| `backends.py` | LLM 後端切換（訂閱登入 / API key）、平行呼叫、JSON 解析 |
| `team.py` | 命令列進入點、決策建議書列印、JSON 存檔 |
| `envfile.py` | 載入 `.env`（必須在其他模組之前匯入） |

沿用既有的 `fetcher.py`（抓價量與基本面）與 `analyzer.py`（技術指標），
不影響原本 `main.py` 的每小時大盤分析流程。
