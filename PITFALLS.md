# PITFALLS.md — 文獻分診台 LitDesk

踩過的坑與刻意的設計取捨。改程式前先看這裡。

---

## 1. `max_tokens: 1000` 會讓分診完全拿不到結果

**症狀**：每一筆都失敗，或回傳空字串。

**原因**：`max_tokens` 涵蓋 thinking + 回應文字，不是只算回應。
Claude Opus 5 預設開啟 adaptive thinking，加上 web search 的多輪往返，
1000 個 token 在思考階段就用完了。

**對策**：`max_tokens` 放到 8000（分診）／12000（對質）。
輸出長度改用別的手段控制：`results` 上限 3 條寫死在 `sanitizeResults`，
prompt 明確要求各欄位精簡，成本用 `effort` 調。

**不要用「關掉 thinking」當解法**：thinking 關閉時模型偶爾會把工具呼叫寫成
純文字，該次呼叫靜默地不會執行、不會報錯，而 P1 完全依賴 web search。
症狀會是「模型說它搜尋了，但拿到的資料是憑空來的」——正好是本專案最嚴重的失敗模式。

---

## 2. 反幻覺閘門在前端，不能只靠 prompt

**規則**：無 `source_quote` 的數字視同「未取得」，不予顯示。

Prompt 寫了不代表模型會照做。實作在 `sanitizeResults()` 做硬性過濾：
沒有 `source_quote` 的 result 整條丟棄，不進資料模型、不進畫面、不進 CSV。
`completeness === '僅標題'` 的紀錄一律清空 `results`。

**改程式時不要繞過這個函式。** 任何新的「顯示效果量」的路徑都要走它。
測試裡有兩條專門盯這件事（stub 故意餵一個杜撰的 `HR 0.55` 和一條無來源句的
`MD -1.2 days`，斷言它們不出現在 DOM 裡）。

---

## 3. 對質台最容易退化成「產出結論的工具」

PRD 明確禁止：不做綜合結論、不做 vote counting、不對效果量取平均、
每個矛盾點必須標篇號。

實作上有兩層：

- **Prompt 層**：P2 的 system prompt 逐條列出禁止事項。
- **程式層**：`sanitizeConflicts()` 丟棄任何 `refs` 為空、或篇號超出範圍的矛盾點。
  篇號會被 `parseInt` 正規化（模型有時回字串 `"2"`）。

前端渲染只顯示四種分類與出處，沒有任何「總結」區塊 —— **不要加。**
若之後覺得輸出太零散，正確方向是改善比較軸的呈現，不是加一段總結。

---

## 4. CSV 的 BOM 和換行都是必需品

Windows Excel 開 UTF-8 CSV，沒有 BOM 就是中文亂碼。實作在 `toCSV()`
前置 `﻿`，並用 CRLF 換行。

**`new Blob([text])` 不會自己加 BOM**，必須在字串裡。
測試直接讀下載檔的前三個位元組驗 `EF BB BF`，改動這段會被抓到。

---

## 5. 進度計數不能用 `state.cards.length`

**踩過**：第一版用 `完成 ${state.cards.length} 筆`。入庫會把成功的卡片從
`state.cards` 移走、失敗的留著，所以第二批跑完時進度顯示的是
「殘留卡片 + 本批」的總數，不是本批筆數。測試直接卡在等 `完成 1 筆`。

**對策**：進度一律用本批的 `queries.length`。

---

## 6. 單筆失敗不能中斷整批

`runTriage()` 的迴圈裡每一筆各自 try/catch。模型呼叫丟例外時，
不是讓整批停下，而是把那一筆轉成失敗卡片（保留例外訊息當 `fail_detail`），
繼續跑下一筆。

**新增任何 await 到迴圈裡時，確認它在 try 內。**

---

## 7. 錯誤訊息要說下一步，不能只說「失敗」

PRD §8 的要求。目前三種失敗各自有對應的下一步文案：

- 付費牆 → 「要判斷請改貼摘要全文。」
- 找不到 → 「請確認 DOI／連結是否正確，或改貼完整標題。」
- 非研究論文 → 「這筆不是原始研究論文，分診台不處理。」

加新的失敗類型時，一起加下一步文案。

---

## 8. 存檔失敗必須讓使用者知道

`saveLibrary()` 包 try/catch，失敗時跳 toast 明確說「本次變更未寫入儲存空間」，
並回傳 `false`。呼叫端不要忽略回傳值就報成功。

接近上限（>3.5 MB）時會提示先匯出備份。

---

## 9. 從瀏覽器直接打 Anthropic API 需要一個特別的 header

`anthropic-dangerous-direct-browser-access: true`，否則 CORS 擋掉。
API key 存在本機（`litdesk:config`），設定頁有清除按鈕與明確警告。
這是「純前端無後端」這個限制的直接後果，不是疏忽。

---

## 10. 伺服端工具會回 `pause_turn`

web search 是 server-side tool，跑久了 API 會回 `stop_reason: "pause_turn"`。
必須把 assistant 的 content 接回 messages 再送一次讓它續跑，
**不要另外加一句「請繼續」**。`callAnthropic()` 有處理，上限 5 次避免無限迴圈。

---

## 11. 測試用的 stub 掛在 `window.LitDeskLLM`

`llm()` 會優先用 `window.LitDeskLLM`，沒有才用真的 API。
測試靠 `page.addInitScript()` 注入，所以不需要 API key、不打網路。

**重構模型呼叫層時保留這個掛勾**，否則整套驗收測試會失效。
