# 上傳與拍攝指引流程設計

> 設計對象：`frontend-engineer`。
> 依據：`docs/technical-plan.md` §5 輸入規格需求、§6（M0 quality gate 已由 backend-engineer
> 完成）。
> 後端依據：`backend/upload_quality.py`（已實作、已測試的真實模組，非規劃中的 API）與
> `backend/README.md`。本文所有 re-shoot 提示文案與欄位名稱，直接取自該模組現有的
> `QualityReport` / `CheckResult` / `messages_zh`，**不是新設計的訊息**，前端應原樣呈現
> `message_zh`，不要重寫或自行翻譯。

## 0. 後端實際回傳的資料形狀（供前端串接對照）

`check_upload_quality(video_path)` 回傳 `QualityReport`：

- `passed: bool` —— 是否安全可排入處理佇列。**`NOT_IMPLEMENTED`（目前只有 court_corners）
  不算失敗**，所以 `passed` 可能在球場角點「未檢查」的狀態下仍為 `True`。
- `checks`: `[fps, camera_stability, court_corners]`，固定順序，每個是一個 `CheckResult`：
  - `status`: `pass` / `fail` / `not_implemented`
  - `message_zh`: 人類可讀的繁體中文提示，**僅在非 PASS 時才有值**
  - `detail` / `metrics`: 英文、給工程/除錯看的，不面向使用者
- `messages_zh`: 依 fps → camera_stability → court_corners 固定順序的訊息陣列，只包含非
  PASS 的項目——這是前端「要顯示哪些提示」最直接的資料來源。

三項檢查現況：

| 檢查 | 狀態 | 會不會擋上傳 |
|---|---|---|
| fps（≥60fps） | 真實檢查 | 會（FAIL → 擋） |
| camera_stability（腳架/晃動啟發式） | 真實但是啟發式，非絕對準確 | 會（FAIL → 擋），但文案要標註「僅供參考」 |
| court_corners（球場四角） | **尚未實作**（cv-engineer M4 工作），目前永遠回傳 `not_implemented` | **不會**擋上傳 |

## 1. 上傳前：拍攝指引畫面

進入上傳頁前（或上傳按鈕旁的常駐提示卡），依 §5 表列三項展示為檢查清單樣式（非可勾選表單，
純提示），對齊 `upload_quality.py` 之後真正會檢查的項目，讓使用者「先看指引、再被 gate
擋下」的體感一致，而不是兩套不同的規則：

```
拍攝前，請確認：

☐ 幀率 60fps 以上
   幀率過低時，球每幀移動距離較大，容易模糊不清。

☐ 腳架固定、機位在底線後方高處
   手持拍攝容易晃動；機位太低或太側，難以看到完整球場。

☐ 只上傳回合片段
   廣告、換場、暖身畫面會干擾分析，請先剪輯掉非比賽內容。

[選擇影片上傳]
```

- 這三點分別對應 §5 表格的「幀率」「機位」「內容」。
- 第三點「純回合片段」目前**沒有**對應的自動檢查（`upload_quality.py` 不檢查內容是否為回合
  片段），純粹是使用者自律提示,文案需避免暗示「系統會自動篩掉非回合片段」。
- 不需要使用者勾選才能繼續——這是提醒，不是表單驗證；真正的驗證發生在上傳後的 quality gate。

## 2. 上傳中

- 檔案選擇/拖放 → 立即開始上傳，顯示進度條。
- 上傳完成後，**不要立刻導向儀表板**——先跑 quality gate（`check_upload_quality`），畫面顯示
  「檢查影片品質中…」的短暫 loading 態（這一步是同步的 OpenCV 讀取，預期是秒級，不是背景
  處理那種分鐘級等待，UI 上可以是一個輕量 spinner，不需要做成長時間任務的進度條）。

## 3. Quality gate 結果畫面

依 `passed` 分兩條路徑：

### 3a. `passed = True`（可能仍帶 not_implemented 提示）

- 直接繼續進入「已送出處理」畫面（進入批次佇列，導向 `dashboard.md` 的空/載入中狀態）。
- **但如果 `messages_zh` 非空**（今天一定會有，因為 court_corners 永遠是 `not_implemented`），
  在送出前插入一個不擋路的確認卡，內容 = 該檢查的 `message_zh` 原文：

  ```
  影片已通過基本檢查，開始上傳處理。

  ⚠ 球場四角偵測功能尚未完成，暫時無法自動確認畫面是否完整涵蓋球場四個角。
    請自行確認拍攝時腳架架設在底線後方高處、四個角都入鏡，
    此項檢查結果不代表通過或未通過。

  [知道了，開始分析]   [我要重新上傳]
  ```

  - 這張卡是**資訊性**的，不是阻擋——按「知道了，開始分析」或直接等幾秒自動繼續都可以（視
    frontend-engineer 實作偏好），但不能讓使用者誤以為「球場角點已檢查過且通過」。
  - 視覺上用「提醒／資訊」樣式（黃色系 info，而非紅色 error），跟下面 3b 的擋路錯誤明顯區分，
    因為這一類不會擋上傳。
  - 若之後 cv-engineer 的真實 court-corner 檢測上線（`court_corners.status` 開始出現真正的
    `pass`/`fail`），這張卡的內容跟出現條件會自動改變（`fail` 會變成 3b 的擋路錯誤；`pass`
    則完全不顯示這張卡）——前端判斷邏輯應該掛在 `status` 而非寫死「court_corners 一定是
    not_implemented」。

### 3b. `passed = False`（至少一項 FAIL）

不進入上傳/處理流程，停留在重拍提示畫面，列出 `messages_zh` 中每一則訊息（對應 FAIL 的
fps 和/或 camera_stability），原文呈現，不重寫：

```
這段影片暫時無法分析，請依下列建議重新拍攝後再上傳：

✕ 偵測到影片幀率約 28 fps，低於建議的 60 fps。
  幀率過低時球體每幀移動距離較大，容易造成嚴重模糊，
  請使用可拍攝 60fps 以上的模式重新拍攝。

✕ 偵測到畫面晃動幅度較大，可能是手持拍攝而非腳架固定。
  請使用腳架固定機位後重新拍攝（此為啟發式偵測，僅供參考，
  非絕對準確）。

[重新選擇影片]   [查看拍攝指引]
```

- 每則訊息前綴一個「✕」或等效的 fail 視覺標記，跟 3a 的「⚠」資訊提醒區分開。
- `camera_stability` 的訊息本身已經在 `message_zh` 里自帶「僅供參考，非絕對準確」——前端
  照原文顯示即可，不需要額外加註，但也不應該把這句免責字樣裁掉（例如为了排版精簡而截斷）。
- 不提供「強制上傳/略過檢查」的按鈕——FAIL 代表 pipeline 大概率會處理失敗或產出不可用結果，
  §5 的設計意圖就是在浪費 GPU 資源前擋下來；若 product-manager 之後要開放「我知道品質不佳，
  仍要上傳」的例外路徑，需要另外定義，現在不做。
- 「查看拍攝指引」導回 §1 的畫面。

## 4. 邊界情況

- **檔案無法開啟 / 非影片格式**：`check_upload_quality` 會丟出 `ValueError`（非 `QualityReport`
  FAIL）。前端應區分「gate 檢查出的可重拍問題」vs「檔案本身有問題（格式錯誤/損毀）」，後者用
  一般錯誤訊息（例如「檔案格式不支援，請確認為常見影片格式後重新上傳」），不要套用 3b 的版面
  语气（那是「拍攝品質」問題，不是「檔案損壞」問題，訊息要對得上原因）。
- **fps 讀不到（`fps <= 0`）**：這也是 FAIL，`message_zh` = 「無法讀取影片幀率，請確認檔案未
  損壞後重新上傳」，走 3b 路徑，跟一般 fps 過低用同一張卡片、同一種視覺樣式即可（同屬
  fps 這個 check 的 FAIL）。
- **camera_stability 幀數過少**（`n_pairs == 0`）：同樣是 FAIL，走 3b。

## 5. 待確認事項

- `check_upload_quality` 目前是同步函式、無佇列/API 包裝（見 `backend/README.md`）。本文假設
  未來會有一個上傳 API endpoint 把它包起来並回傳 JSON 版的 `QualityReport`——實際 endpoint
  路徑/回傳格式需要跟 `backend-engineer` 對齊，本設計只依賴已確定的欄位名稱
  （`passed`、`checks[].status`、`checks[].message_zh`）。
