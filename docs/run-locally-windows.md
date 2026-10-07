# 在自己的 Windows 電腦上跑完整流程（上傳 → 分析 → 儀表板）

2026-10-07。延續你之前已完成的設定（已 clone 到 `C:\Users\roy33\rally-ai`、已建立虛擬環境 `rally-venv`）。全部免費，不需要 GPU。

## 0. 更新程式碼（每次開始前做一次）

開 PowerShell：

```powershell
cd C:\Users\roy33\rally-ai
git pull
.\rally-venv\Scripts\Activate.ps1      # 看到 (rally-venv) 就對了；若你的 venv 在別處，換成那個路徑
pip install -r requirements.txt
$env:PYTHONUTF8 = "1"
```

> 如果 `Activate.ps1` 被擋（執行原則），先執行一次：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

## 1. 視窗 A：啟動網站與 API

```powershell
uvicorn backend.api.upload:app --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 就好了，這個視窗**不要關**。

## 2. 視窗 B：啟動分析程式（worker）

另開一個 PowerShell，重複第 0 步的 `cd`、`Activate.ps1`、`$env:PYTHONUTF8 = "1"`，然後：

```powershell
python -m backend.worker --ball-filter --merge-within-s 0.5 --classify-strokes
```

- `--ball-filter --merge-within-s 0.5`：目前測起來誤報最少的擊球偵測設定。
- `--classify-strokes`：標正反手。**只有上傳時選了「右手」或「左手」的影片才會標**；選「不確定」的不會標。
- 這個視窗也**不要關**；它每 2 秒檢查一次有沒有新影片。

## 3. 用瀏覽器上傳

1. 用 **Chrome 或 Edge** 開 `http://localhost:8000/`（會自動進到上傳頁）。
2. 選持拍手（右手／左手），再按「選擇影片上傳」。
3. 上傳後按「知道了，開始分析」→ 點「前往儀表板查看分析進度」。
4. 儀表板會顯示「分析中」，每 3 秒自動更新；一支 20–60 秒的影片大約要 **1–3 分鐘**（視電腦而定）。

## 4. 常見狀況

| 狀況 | 原因／處理 |
|---|---|
| 上傳後顯示「重拍」提示，說幀率不足 | 品質檢查要求 ≥ 60 fps。用手機的 60 fps 模式重拍；舊的 30 fps 影片過不了這一關（這是刻意的，見 `docs/acceptance-footage-spec.md`） |
| 儀表板一直「分析中」 | 視窗 B 沒在跑，或跑到一半出錯——看視窗 B 的訊息 |
| 儀表板顯示「處理失敗」 | 展開「技術細節」，把那段英文貼給我 |
| 影片無法播放但擊球清單有出來 | 少數瀏覽器不支援該影片編碼；改用 Chrome／Edge |
| 正反手顯示「推測：」 | 信心低的猜測，參考就好；正反手仍是實驗功能 |

## 5. 想直接測已經在資料夾裡的舊影片（不經過 60 fps 檢查）

舊影片（30 fps）會被上傳頁擋下。要看它們的分析結果，用之前的掃描指令即可（不經過網頁）：

```powershell
$env:RALLY_VIDEO_DIR = "C:\rally-videos"
python -m backend.library scan --shots --ball-filter --merge-within-s 0.5
```

## 6. 標註影片（拍好驗收影片之後）

```powershell
$env:RALLY_VIDEO_DIR = "C:\rally-videos"      # 放驗收影片的資料夾
python -m labeling.label_server
```

用 Chrome／Edge 開 `http://localhost:8001/`。按鍵：←／→ 一幀、Shift+←／→ 十幀、空白鍵播放／暫停、`F` 正手、`B` 反手、`O` 其他、`Delete` 刪除最近的標記。完整說明見 `labeling/README.md`。

- 第一次打開一支影片要先做「標記用影片」，30 秒的影片約 15–20 秒。
- 若出現錯誤說無法建立 VP8 影片：代表你電腦上的 OpenCV 不含該編碼器（在 Linux 測過可以，Windows 尚未驗證），把錯誤訊息貼給我。

## 7. 你的影片不會離開你的電腦

這套流程完全在你自己的電腦上跑：影片存在 `C:\Users\roy33\rally-ai\data\uploads\`，資料庫在 `data\rally.sqlite3`，兩者都被 `.gitignore` 擋住，不會被推到 GitHub。
