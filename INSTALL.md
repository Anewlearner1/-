# 本地安裝

投資團隊（`team.py`）**預設不需要 Anthropic API key** —— 它透過你電腦上已登入的
Claude Code CLI 執行，吃你現有的 Claude 訂閱額度。

## 需要準備

| 項目 | 說明 |
|---|---|
| Python 3.10+ | `python3 --version` 確認 |
| Node.js 18+ | 只為了裝 Claude Code CLI；`node --version` |
| Claude 訂閱帳號 | Pro / Max 皆可 |

## 步驟

### 1. 取得程式碼

```bash
git clone https://github.com/Anewlearner1/-.git tw-stock-team
cd tw-stock-team
git checkout claude/investment-team-setup-wh2rmq
```

### 2. 建虛擬環境並安裝套件

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows（PowerShell）：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 安裝並登入 Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

瀏覽器會開起來，用你的 Claude 帳號登入即可。驗證：

```bash
claude --version
```

**這一步就是「不用 API key」的關鍵** —— 登入狀態存在你本機，
`team.py` 會透過 Claude Agent SDK 直接使用它。

### 4. 開第一場會

```bash
python team.py --list-team          # 先看五位分析師的背景卡（不花額度）
python team.py 2330.TW              # 真的開一場會
```

第一次跑約 1-3 分鐘（五人 × 兩輪，平行執行）。結果會存到
`reports/team/team_meeting_<時間戳>.json`。

## 常用選項

```bash
python team.py 2330.TW 2454.TW      # 一次討論多檔
python team.py 2330.TW --period 3mo # 拉長取樣區間
python team.py 2330.TW --no-cross-exam   # 只跑第一輪，速度與額度減半
TEAM_EFFORT=medium python team.py 2330.TW  # 降低思考強度，更省更快
```

## 選用設定（`.env`）

複製範本後按需要填寫，`team.py` 啟動時會自動載入：

```bash
cp .env.example .env
```

| 變數 | 用途 |
|---|---|
| `TEAM_BACKEND` | `claude_cli`（預設，免 API key）或 `api` |
| `TEAM_EFFORT` | `low` / `medium` / `high`（預設）/ `xhigh` / `max` |
| `TEAM_MODEL` | 模型；`claude_cli` 用別名如 `opus`、`sonnet` |
| `REPORT_DIR` | 報告輸出目錄，預設 `./reports` |
| `ANTHROPIC_API_KEY` | 只有 `TEAM_BACKEND=api` 或跑 `main.py` 才需要 |

`.env` 已列入 `.gitignore`，不會被 commit。

## 疑難排解

**`找不到 claude 指令`**
CLI 沒裝或不在 PATH。重跑 `npm install -g @anthropic-ai/claude-code`，
並確認 `npm bin -g` 的路徑有加進 PATH。

**`Claude CLI 沒有回傳結果訊息（可能是登入過期）`**
重跑 `claude login`。

**`技術面：資料不足` 或基本面抓取失敗**
yfinance 或 twse.com.tw 連不上（防火牆、公司網路、代理伺服器）。
先用 `python -c "import yfinance; print(yfinance.Ticker('2330.TW').history(period='5d'))"`
單獨測試。抓不到資料時五位分析師仍會發言，但信心度會全面偏低 —— 別拿那種結果做決策。

**跑很慢**
`TEAM_EFFORT=low` 或 `--no-cross-exam`。預設 `high` 是為了分析品質。

**想改用 API key 計費**
`.env` 填 `ANTHROPIC_API_KEY` 並設 `TEAM_BACKEND=api`。
此模式會走 Messages API，按 token 計費。

## 原本的每小時大盤分析（`main.py`）

那支仍需要 `ANTHROPIC_API_KEY`（走 Messages API），與投資團隊互不影響：

```bash
python main.py --once
```
