# 權證篩選器

依「硬性門檻 → 加分項評分」流程，從權證清單中篩出候選標的。

## 快速開始
```bash
pip install pandas numpy
python warrant_screener.py --demo                 # 用虛構資料測試
python warrant_screener.py --template             # 產生空白 CSV 範本
python warrant_screener.py my_data.csv            # 篩選你的資料
python warrant_screener.py my_data.csv --min-score 3 --issuers 元大 凱基 --save-all
```

## CSV 欄位
必要：warrant_code, underlying, issuer, warrant_type(認購/認售), strike,
underlying_price, days_to_expiry, leverage, bid, ask, avg_volume_5d,
underlying_volume, iv(%), delta

選填（缺少時對應加分項不得分）：hv20(%), outstanding_pct(%),
days_to_event, event_before_expiry(1/0)

## 硬性門檻（預設，可用參數或修改檔內 HardFilter 調整）
| 項目 | 預設 |
|---|---|
| 剩餘天數 | >= 90 |
| 實質槓桿 | 3 ~ 6 |
| 價外幅度 | 0 ~ 15% |
| 價差比 | <= 1% |
| 權證 5 日均量 | >= 100 張 |
| 標的日均量 | >= 3000 張 |

## 加分項（每項 1 分，滿分 6）
1. 同標的 IV 最低（僅在通過硬性門檻者間比較）
2. IV 低於 20 日 HV
3. Delta 落在 0.4 ~ 0.6
4. 流通在外比例 <= 50%
5. 距催化事件 >= 14 天
6. 事件日在到期日之前

排序：分數高 → 價差比低 → IV 低。

## 輸出
- warrant_candidates.csv：達 --min-score 的候選
- warrant_full_result.csv（加 --save-all）：含被剔除者與剔除原因

## 資料來源提醒
權證的 IV、Delta、槓桿、流通在外比例等，可從券商權證專區或
權證資訊網站（如證交所、各發行券商網站）匯出後整理成 CSV。
本工具僅為輔助篩選，不構成投資建議。

---

# 資料來源層（data_sources.py）

## 為什麼是「適配器」而不是直接連 API
- 權證的 **IV、實質槓桿、流通在外比例** 沒有穩定且有文件的公開 API，
  最可靠的做法是從券商／權證網站匯出，再用「欄位對應」轉成標準格式。
- 標的股的行情與成交量有證交所／櫃買官方 OpenAPI，但**端點不寫死**：
  腳本在執行時讀官方 `swagger.json` 依關鍵字探索，官方改版也不會靜默出錯。
- 官方 OpenAPI 多半只提供「當期」資料，**歷史要自己累積**（HV 需要日價序列）。

## 使用流程
```bash
pip install requests pandas numpy

# 1) 產生欄位對應範本，把右邊改成你匯出檔的實際欄位名稱
python data_sources.py mapping-template

# 2) （選用）查官方 OpenAPI 有哪些端點
python data_sources.py discover 收盤
python data_sources.py discover 權證
python data_sources.py discover 收盤 --tpex     # 櫃買

# 3) 轉成標準格式，並用官方資料補標的股價/成交量、用自有日價算 HV
python data_sources.py build raw.csv --map mapping.json -o warrants.csv \
       --enrich --hv-csv daily_prices.csv

# 4) 篩選
python warrant_screener.py warrants.csv --min-score 4
```

## 欄位缺失時的行為
| 缺少的欄位 | 影響 |
|---|---|
| avg_volume_5d / underlying_volume | 對應硬性門檻無法通過（NaN 視為不合格） |
| hv20 | 「IV 低於 HV」不得分 |
| outstanding_pct | 「流通在外低」不得分 |
| days_to_event / event_before_expiry | 兩個事件相關加分項不得分 |

`build` 會在轉換後列出空值超過 50% 的欄位，提醒你哪些條件實際上已失效。

## 尚未驗證的部分（data_sources.py 的 --enrich）
`data_sources.py --enrich` 走的是 `openapi.twse.com.tw`，開發環境連不到該網域，
因此 `underlying_snapshot()` 的端點命中與欄位名稱未實測。若欄位名不符，
它會列出「實際欄位」並報錯，只需修改 `_find_col` 的候選清單。

**要接真實資料，建議直接用下面的 `twse_live.py`**，它走的是實測可用的
`www.twse.com.tw/exchangeReport/*`。

已用模擬資料驗證：欄位對應、千分位／百分比／破折號清洗、由到期日推算天數、
補值不覆蓋原資料、HV 計算（與理論值吻合）、對應錯誤的報錯訊息、
兩支腳本端對端銜接。

---

# 真實資料層（twse_live.py）

直接向證券交易所抓真實行情，不再需要從券商匯出報價。

## 一分鐘上手
```bash
pip install requests pandas numpy

# 1) 看一眼真實行情（最近一個交易日的認購＋認售權證）
python twse_live.py quotes --head 20

# 2) 產生靜態資料範本，填入履約價／到期日／行使比例
python twse_live.py static-template            # -> warrant_static.csv

# 3) 組出篩選器要的標準 CSV（真實行情 + 靜態資料 + 自算 IV/Delta/槓桿）
python twse_live.py build -o warrants.csv --days 5 --static warrant_static.csv --hv

# 4) 篩選
python warrant_screener.py warrants.csv --min-score 3
```

## 哪些欄位是真的從證交所來的

| 欄位 | 來源 | 狀態 |
|---|---|---|
| warrant_code / warrant_type | MI_INDEX `type=0999`(認購) / `0999P`(認售) | 真實 |
| bid / ask | 同上：最後揭示買價／賣價 | 真實 |
| avg_volume_5d | 同上，往回抓 N 個交易日的成交股數平均（股→張） | 真實 |
| underlying / underlying_price | 同上：標的代號、標的收盤價 | 真實 |
| underlying_volume | STOCK_DAY_ALL 當日成交股數（股→張） | 真實 |
| hv20 | STOCK_DAY 逐月日收盤價 → 對數報酬年化標準差 | 真實（`--hv`） |
| issuer | 由權證簡稱推測（「南亞統一59購01」→ 統一） | 推測，實測 31,449 檔全數命中 |
| **strike / days_to_expiry / exercise_ratio / outstanding_pct** | **證交所未公開，需 `--static` 提供** | 需自備 |
| iv / delta / leverage | **由上列資料自行以 Black-Scholes 反解** | 計算值 |

重點：只要你提供「履約價 + 到期日 + 行使比例」這三個幾乎不會變動的靜態欄位，
IV、Delta、實質槓桿就會由**真實市價**算出來，不必再相信券商匯出的二手數字。
沒有靜態資料時，這四欄（含 strike）會是空值，對應的硬性門檻與加分項自動失效，
`build` 結束時會列出每個欄位的空值比例提醒你。

## 實測可用的端點
```
每日收盤行情（權證）
  GET www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=YYYYMMDD&type=0999
  type: 0999 認購(不含牛證) / 0999P 認售(不含熊證) / 0999B 牛證 / 0999C 熊證
        0999X 可展延牛證 / 0999Y 可展延熊證
個股月成交資訊（算 HV 用）
  GET www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMM01&stockNo=2330
全市場個股當日行情
  GET www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json
```

## 證交所會間歇性擋請求
同一個網址上一分鐘成功、下一分鐘被 307 導向「因為安全性考量，您所執行的頁面
無法呈現」是常態（實測：`type=ALLBUT0999` 成功 → 35 秒後 `type=0999P` 被擋
→ 再 35 秒 `type=0999` 又成功）。`twse_live.py` 因此：
- 所有請求序列化，預設間隔 **4 秒**（`--gap` 可調大）
- 307 視為可重試，退避時間逐次加長，重試用完才放棄
- 全部結果落地到 `.cache_twse/`，同一交易日只會真的抓一次，重跑不重抓
- 某一個類別（例如認售）整個抓不到時，只警告並繼續，不會讓整批陣亡；
  最後的覆蓋率報告會讓你看到少了什麼
- 全部類別都失敗才丟 `ThrottledError`，訊息直接告訴你是被限流

被擋了就等 5–10 分鐘、把 `--gap` 調到 10–20 再跑；已抓到的資料不會白費。

另外 `STOCK_DAY_ALL` 就算帶 `response=json` 也是回 **CSV**（實測），
所以解析層 JSON／CSV 兩種都接。

## 靜態資料 CSV 格式
欄位名中英文皆可（`warrant_code`/`權證代號`、`strike`/`履約價`…）：

```csv
權證代號,履約價,到期日,行使比例,流通在外比例
030079,2600,2027-03-18,0.01,35
030081,270,115/01/15,0.05,22
```
- 到期日支援西元（`2027-03-18`）與民國（`116/03/18`）
- 行使比例沒填時預設 1
- 也可以直接給 `days_to_expiry`／`剩餘天數` 取代到期日
- 選填 `days_to_event`、`event_before_expiry`（催化事件那兩個加分項）

這份表通常從發行券商的權證專區或權證資訊揭露平台匯出一次，
之後只有新掛牌的權證需要補，行情則每天重抓。

## 常用參數
```bash
python twse_live.py --gap 10 build -o warrants.csv \
    --days 10 \                  # 均量取 10 個交易日
    --types 0999 0999P 0999B \   # 也納入牛證
    --underlyings 2330 2317 \    # 只看這幾檔標的（大幅減少請求量）
    --static warrant_static.csv \
    --hv --hv-months 3 \         # 算 HV20，每檔標的 3 個請求
    --rate 0.015                  # 無風險利率

python twse_live.py hv 2330 2317 -o hv.csv     # 只算 HV
```
`--hv` 是每檔標的數個請求，全市場跑會很久；先用 `--underlyings` 或
先跑一次不帶 `--hv` 的硬性門檻，再針對存活的標的算 HV。

## 已驗證 / 未驗證
**已實測（對真實的 www.twse.com.tw，交易日 2026-09-18）：**
- `MI_INDEX?type=0999` → **31,449 檔真實認購權證**，欄位名與解析完全吻合
- 完整 `build` 跑通：31,449 檔輸出成標準 CSV，
  標的股價 0% 空值、標的成交量 1% 空值、發行商 0% 空值
- `STOCK_DAY_ALL` → 實際回 CSV（已處理），標的量價正確併入
- `STOCK_DAY` → `hv 2330 2317` 得到 HV20 = 18.84% / 22.67%
- 間歇性 307 行為，以及本模組的重試、容錯與錯誤訊息

**未能實測：`type=0999P`（認售）。** 這個代碼取自證交所 mi-index 頁面自己的
下拉選單（`0999P 認售權證(不含熊證)`），但從本開發環境連過去一律被 307 擋掉，
即使前後相鄰的 `0999`、`ALLBUT0999` 都成功。無法區分是「該網路被針對性擋」
還是「此端點不支援該代碼」。請在你自己的網路上先跑：

```bash
python twse_live.py --types 0999P quotes --head 5
```

- 有資料 → 直接用預設的 `--types 0999,0999P`
- 仍被擋 → 先用 `--types 0999` 只做認購；`build` 會警告少了認售並照常輸出
  （全市場約有 2,300 檔認售權證，代號結尾為 T／U／P）

已用固定樣本（fixtures）離線驗證：
- 行情解析、5 日均量彙總、標的量價併入、靜態資料合併、覆蓋率報告
- Black-Scholes 價格↔IV 往返誤差 < 1e-8、買賣權平價誤差 < 1e-14
- Delta 範圍、無套利區間外回 NaN、HV 與理論波動率吻合（40% → 40.6%）
- 民國日期、千分位／HTML 標記清洗、發行商推測
- 與 `warrant_screener.py` 端對端銜接（權證代號前導 0 不會掉）

牛熊證（`0999C`／`0999B`／`0999X`／`0999Y`）同樣未實測，它們與 `0999` 是同一
端點同一組欄位，代碼與「牛證屬認購、熊證屬認售」的對應取自證交所頁面的選單。

離線測試隨時可重跑：
```bash
python test_twse_live.py     # 41 項檢查，不連網
```
