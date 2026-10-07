# Rally AI

網球影片分析系統：影片輸入 → 逐拍統計（擊球數、正反拍、球速）→ 疊圖影片輸出。完整技術規劃見 [`docs/technical-plan.md`](docs/technical-plan.md)。

**授權：AGPL-3.0**（見 [`LICENSE`](LICENSE)）。2026-09-30 使用者裁示接受開源，以換取可直接使用 YOLOv8/Ultralytics 做球員偵測，不需購買商業授權——決策全文見 [`docs/decisions/0001-player-detection-license.md`](docs/decisions/0001-player-detection-license.md)。`LICENSE` 是 AGPL-3.0 完整正式條文（2026-10-07 換上；gnu.org 在本環境被擋，取自 Ultralytics repo 與 choosealicense.com 兩份副本，逐字比對一致；與 SPDX 版本只差 http/https）。SPDX-License-Identifier: AGPL-3.0-or-later。

這是一個獨立專案，跟同帳號下的 `tennis-form-coach`（單人 MediaPipe 評分/量測 CLI 工具）是不同的系統——規模大很多，包含球員偵測追蹤、TrackNet 球追蹤、球場 homography 校正、前端儀表板等，兩者除了同樣是網球影片分析之外，程式碼與架構不共用。

## 快速開始

- 在自己的 Windows 電腦上跑完整流程（上傳 → 分析 → 儀表板）：[`docs/run-locally-windows.md`](docs/run-locally-windows.md)
- 驗收影片怎麼拍：[`docs/acceptance-footage-spec.md`](docs/acceptance-footage-spec.md)
- 目前進度：[`docs/status.md`](docs/status.md)

## 團隊（`.claude/agents/`）

| Subagent | 角色 | 負責範圍 | 對應里程碑 |
|---|---|---|---|
| `cv-engineer` | 電腦視覺工程師（核心） | 球追蹤、姿態、球場校正、落點偵測、球速演算法 | M1、M4、M5 |
| `ml-engineer` | 機器學習工程師 | 擊球時間點偵測、正反拍分類模型訓練與評估 | M2、M3 |
| `backend-engineer` | 後端工程師 | 影片上傳、批次處理佇列、GPU 排程、API、資料庫 | 全程 |
| `frontend-engineer` | 前端/App 工程師 | 儀表板 UI、影片同步播放、上傳與拍攝指引流程 | M6 |
| `mlops-engineer` | MLOps/DevOps | GPU 雲端部署、模型版本管理、成本監控 | M4 之後 |
| `data-labeler` | 資料標註人員 | 標註擊球時間點、球位置、球場角點 | M2～M5 |
| `tennis-domain-consultant` | 網球領域顧問 | 定義擊球分類標準、驗證結果是否合理 | M3、M5 |
| `product-manager` | 產品經理 | 需求定義、里程碑排程、驗收標準、風險控管 | 全程 |
| `ui-ux-designer` | UI/UX 設計師 | 儀表板與拍攝指引的設計 | M6 前 |
| `qa-engineer` | QA | 不同場地、光線、機位的測試 | M2 之後 |

每個 subagent 的 system prompt 都直接引用 `docs/technical-plan.md` 裡對應章節的具體內容（驗收標準、已知限制、風險），不是只有角色名稱的空殼。用 Claude Code 的 Agent 工具、指名對應的 subagent 名稱即可呼叫。

## 里程碑（見 technical-plan.md §6）

M1～M3 不需要球追蹤，可先驗證需求；M4～M5（球場校正、球速）技術風險最高，應預留較多時間。

## 已知風險（見 technical-plan.md §7）

訓練資料與手機側拍落差大、標註成本、單鏡頭球速精度有限（只能當估計值）、GPU 成本、現有開源網球專案多為研究原型不能直接上線。授權風險另見 §4——YOLOv8 是 AGPL-3.0，商用前需法務確認。
