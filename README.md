# tennis-form-coach

從一般手機拍攝的網球影片分析揮拍技術。以 MediaPipe 姿態偵測為基礎，分成兩條互相獨立、共用前半段（姿態 → 特徵 → 揮拍分段）的管線：

1. **量測管線**（已實作）── 回答「多快、什麼角度」：揮拍（拍頭）速度、手腕速度、擊球初速、擊球角度。詳見 `tennis_coach/kinetics.py`。
2. **評分管線**（尚未在目前分支重建）── 回答「動作好不好」：對照校準過的評分表（正拍/反拍/發球），給 0-100 分與具體建議。

## 儲存位置

這個專案目前只存在於 GitHub 的 **`Anewlearner1/-`** repo 的 **`tennis-form-coach`** 分支：

```
git clone https://github.com/Anewlearner1/-
cd -
git checkout tennis-form-coach
```

之所以借放在這個看似無關的 repo，是因為執行環境目前沒有替這個帳號建立新 repo 的權限（GitHub App 帳號層級 403，非單一 repo 的授權問題）。這個分支跟該 repo 原本的股票分析專案完全獨立，互不共用檔案。**修改這個專案時只在 `tennis-form-coach` 分支上工作，不要合併回股票分析用的分支。**

## 安裝

```bash
pip install -r requirements.txt
```

MediaPipe 需要系統的 OpenGL/EGL 函式庫；缺少時會在執行期噴 `libEGL.so.1: cannot open shared object file`，用套件管理員裝 `libegl1 libgl1 libgles2`（Debian/Ubuntu 系）即可。

姿態模型會在第一次執行時自動下載到 `~/.cache/tennis-form-coach/`（可用環境變數 `TENNIS_COACH_CACHE` 覆寫）。

## 使用

```bash
tennis-coach speed 影片.mp4
tennis-coach speed 影片.mp4 --hand right --json out/speed.json
```

## 已知限制

- **擊球角度與擊球初速只有側面機位才可信。** 從背面或正面拍攝時，球主要沿著鏡頭深度方向飛行，2D 像素追蹤量不到這個方向的位移量，會嚴重低估球速；角度公式本身也只在側面拍攝時等同真正的擊球角度。拍頭速度／手腕速度不受此限制，因為是從 MediaPipe 估計的 3D 世界座標算出。
- 球的追蹤（`ball.py` 的 `track_from`）用顏色與運動預測抓球，光線或球衣顏色接近球色時可能誤判，目前沒有自動偵測「追錯東西」的機制。
- 評分管線（`rubrics/*.yaml` 與相關模組）尚未在這個分支重建，只有量測管線是完整、測試過的。

## 開發

```bash
pytest
```

測試用 `tests/synth.py` 產生的合成姿態序列（有精確可算的期望值）驗證量測邏輯，另有需要真實影片檔的整合測試（`test_cli.py`、`test_kinetics.py` 的球追蹤部分）。

新增功能或修 bug 時，`.claude/agents/software-engineer.md` 列了這個專案已經踩過雷、必須遵守的慣例（例如姿態座標系的陷阱、球速回傳 `None` 而非亂猜的規則）；規劃或拆解任務時可用 `.claude/agents/project-manager.md`。
