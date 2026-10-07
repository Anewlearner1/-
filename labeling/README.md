# labeling/ — 擊球標記工具

## 給擁有者：用網頁標記每一拍（2026-10-07 新增，建議用這個）

這個網頁讓你在自己的 Windows 電腦上，用 Chrome 或 Edge 標出影片裡**每一次球拍碰到球**
的那一幀，以及正拍／反拍／其他。標記一律用 **OpenCV 解碼器的幀號**：網頁播放的是一份
特製的「標記用影片」，每一幀上方有一條黑白條碼、左下角有「decoder frame N」，網頁直接從
畫面讀條碼來決定現在是第幾幀，不相信瀏覽器的播放時間（之前用播放器幀數標記，差了約 3 幀，
見 `docs/real-footage-findings.md`）。

### 啟動（Windows）

1. 第一次使用：在專案資料夾開「命令提示字元」或 PowerShell，執行
   `pip install -r requirements.txt`。
2. 把影片放進 `data\videos\`（或執行 `python -m backend.library ingest 影片路徑`），
   或者指定別的資料夾：
   - 命令提示字元：`set RALLY_VIDEO_DIR=D:\網球影片`
   - PowerShell：`$env:RALLY_VIDEO_DIR="D:\網球影片"`
3. 在專案資料夾執行：`python -m labeling.label_server`
   （換埠號：`python -m labeling.label_server --port 8002`）
4. 用 Chrome 或 Edge 開 `http://localhost:8001/`。
5. 點左邊的影片。第一次開某支影片要先產生標記用影片，長影片可能要等一兩分鐘
   （存在 `data\label_proxies\`，之後直接用）。

### 按鍵

| 按鍵 | 作用 |
|---|---|
| `←` / `→` | 上一幀／下一幀 |
| `Shift` + `←` / `→` | 往前／往後 10 幀 |
| 空白鍵 | 播放／暫停 |
| `F` | 在目前這一幀標「正拍」 |
| `B` | 在目前這一幀標「反拍」 |
| `O` | 在目前這一幀標「其他」（不確定、截擊、發球等） |
| `Delete` | 刪除離目前這一幀最近的標記 |

也可以在「跳到幀」輸入幀號，或點右邊標記清單裡的一項跳過去。

### 標記步驟

1. 從頭播放或逐幀看，每次球拍碰到球就停在那一幀，按 `F`／`B`／`O`。
   同一幀再按一次別的鍵會改成新的種類。
2. 大字「解碼器幀 #N」就是要記的幀號。若出現黃色警告「讀不到幀號條碼」，
   先按 `←`／`→` 讓它重新讀到，再標記（讀不到時網頁不會讓你標）。
3. 選持拍手（右手／左手）。
4. **從頭看到尾、每一拍都標了**，才勾「我已從頭看到尾，所有擊球都標了」。
   沒勾也能存，但會存成「未完成」，評估時不能拿來算漏抓。
5. 按「儲存」。檔案寫到 `labeling\labels\<影片檔名>.json`，旁邊另有
   `<影片檔名>.meta.json` 記錄是否完成、持拍手、日期與幀號制（`opencv_decoder`）。
   覆蓋舊檔前，舊檔會先備份到 `data\label_backups\`。
6. 已經有舊標記、但幀號不是解碼器幀號的影片，清單上會標「舊標記（非解碼器幀號）」，
   開啟時不會載入那些舊幀號，請重新標記。

### 給開發者

- `labeling/proxy.py`：`make_label_proxy(video_path, out_path)` 以 `cv2.VideoCapture`
  逐幀解碼，縮到長邊不超過 960 px，在畫面上方**加**一條 32 px 高的條碼（不蓋住畫面），
  寫成 VP8 WebM，寫完會再解碼一次檢查每一幀的條碼都等於幀序號。條碼：全寬平均分成 28 格
  純黑白，第 0 格白、第 1 格黑（參考），第 2–21 格是幀號 20 位元（高位在前，白 = 1），
  第 22–25 格是 4 位元檢查碼（幀號五個 4 位元段的 XOR），第 26 格白、第 27 格黑。
  讀取時取每格中央、以黑白參考格的中點為門檻，檢查碼不符就當作讀不到。
  `labeling/label_logic.js` 實作同樣的讀法，兩邊要一起改。
- `labeling/label_server.py`：FastAPI。`GET /api/videos`、`GET /api/videos/{name}/info`、
  `GET /api/videos/{name}/proxy`（支援 Range）、`GET/POST /api/videos/{name}/label`。
  名稱一律經過 `backend.library.resolve_video`。重複或超出範圍的幀號、fps 不符回 422。
- 標記檔格式：`{video_id, fps, contact_frames, stroke_labels, racket_hand, source: "real"}`，
  `ml/eval_shot_timing.load_label` 與 `ml/eval_stroke_classification` 都讀得到。
- 測試：`tests/test_label_proxy.py`、`tests/test_label_server.py`、
  `tests/test_label_logic.js`、`tests/test_label_browser.py`（真的開 Chromium，逐幀與跳幀後比對
  網頁顯示的幀號、螢幕截圖上的條碼與要求的幀號；沒有 Chromium 時略過）。

---

## Older tool: `label_shots.py` (semi-automated, English notes)

**Status: tooling only. No real video has been labeled with this tool yet.**
Nothing in this directory is a delivered dataset — there is no
`labeling/labels/` folder of finished clips, and `ml/shot_timing.py`'s
accuracy against real footage is still unvalidated (see `ml/README.md`,
"Blocked", item 2). This is the mitigation the technical plan calls for
(`docs/technical-plan.md` §7: labeling cost is a named top risk, and the
explicit mitigation is building semi-automated tooling before labeling by
hand) — building it is the deliverable here, not a labeled clip.

## What it produces

The exact JSON shape ml-engineer specified in `ml/README.md` under
"Interface needed from data-labeler", nothing more and nothing less:

```json
{"video_id": "clip_0007", "fps": 30.0, "contact_frames": [42, 118, 203, 275]}
```

Provenance (how the label was produced — which candidates came from
`detect_shots()`, how many a human confirmed/rejected, who reviewed it and
when) lives in a **separate sibling file**, `<video_id>.meta.json`, next to
the label, so it never changes the label's own shape. Its `"source"` field
is `"detect_shots+human_correction"` for every label this tool produces —
there is currently no fully-manual labeling path, so that is always what
you'll see until one exists.

## Why "semi"-automated

`ml/shot_timing.py::detect_shots()` is already a real, working peak
detector — just unvalidated against real labels. Rather than having a human
mark every contact frame on a blank timeline, this tool runs
`extract_player_landmarks()` + `detect_shots()` first to get *candidate*
contact frames, and has a human confirm, correct, or add to that — the
bootstrap-then-correct workflow the data-labeler role brief calls for.

(The sibling `tennis-form-coach` project has a simpler contact-frame
correction tool for a related purpose sketched as `correct.py` — it was not
actually built there either, so there was nothing to reuse; this tool is a
fresh implementation for Rally AI, written for `PlayerLandmarkSequence` /
`ShotTimingResult`, not imported from that separate codebase.)

## Running it on a real video

Three stages, each a CLI subcommand of `labeling/label_shots.py`:

### 1. Extract candidates

```bash
python -m labeling.label_shots extract path/to/clip_0007.mp4 labeling/sessions/clip_0007
```

This runs `extract_player_landmarks()` + `detect_shots()` on the video and
writes, under the given output directory:

- `candidates.json` — the bootstrapped, **unreviewed** candidate list: one
  entry per `detect_shots()` peak, each with `frame`, `time_s`,
  `peak_speed`, `wrist`, `status: "pending"`, and `image` (path to a still).
- `frames/candidate_NNNNNN.jpg` — one still image per candidate, grabbed
  directly from the source video, with the frame number, timestamp, and
  which wrist burned into the image (and into the filename) so a reviewer
  can tell candidates apart at a glance without a video player. This
  mirrors how frames were manually reviewed in the sibling
  `tennis-form-coach` project.

Pass `--no-images` to skip the still-image dump (e.g. for a quick
detector-only check) and `--video-id` to override the default
(the video's filename stem).

### 2. Human review

**What the reviewer needs to do:** open `frames/` and look at each
candidate still next to the real video around that timestamp, then decide,
per candidate, whether it's a genuine ball-contact frame. Two ways to
record that:

- **Interactive CLI:**

  ```bash
  python -m labeling.label_shots review labeling/sessions/clip_0007/candidates.json
  ```

  Walks every `"pending"` candidate, prints its frame/time/wrist/image
  path, and asks `keep(k) / drop(d) / skip(s)`. At the end it asks for any
  missed contact frames (comma-separated frame numbers, found by scrubbing
  the real video) and appends them as `"status": "added"` entries.

- **Hand-edit `candidates.json` directly:** set each candidate's
  `"status"` to `"confirmed"` (it's a real contact) or `"rejected"` (it's
  not — e.g. a split-step or serve ball-toss, the false-positive failure
  mode `ml/README.md` calls out for wrist-speed-only detection). Append a
  new object with `"status": "added"` for any contact `detect_shots` missed
  (frame, and `time_s = frame / fps`; `peak_speed`/`wrist` can be `null`).

Every bootstrapped candidate must end up `"confirmed"` or `"rejected"` —
`finalize` refuses to run while any are still `"pending"`, precisely so a
label can't accidentally include an unreviewed guess.

### 3. Finalize

```bash
python -m labeling.label_shots finalize labeling/sessions/clip_0007/candidates.json \
    --out labeling/labels/clip_0007.label.json --reviewer "your name"
```

Writes the final label JSON (exact three-key shape above) and the sibling
`clip_0007.meta.json` provenance file (source, reviewer, reviewed-at
timestamp, and counts of confirmed/rejected/added candidates).

## For ml-engineer / qa-engineer consuming labels later

- Only trust a `<video_id>.label.json` if its sibling `.meta.json` exists
  and has `"source": "detect_shots+human_correction"` — that's the only
  provenance this tool currently produces (no fully-manual path exists
  yet). A label file with no matching `.meta.json` next to it wasn't
  produced by this tool and its provenance is unknown.
- `n_rejected` in the meta file is a rough proxy for how often
  `detect_shots()`'s wrist-speed-only heuristic is wrong on that clip
  (false positives per `ml/README.md`'s "Blocked" item 1) — worth tracking
  across clips once real labeling starts.

## Tests

`tests/test_label_shots.py` covers the plumbing — building candidates from
a `detect_shots` result, writing/reading `candidates.json`, the
finalize-refuses-while-pending guard, and an end-to-end
extract → (stubbed) human correction → finalize flow producing a
schema-valid label — using `tests/synth_pose.py`'s synthetic landmark
fixture rather than a real video (no video I/O or mediapipe model download
needed for these tests). The human-correction step in that end-to-end test
is a stub (statuses set directly, as a JSON-editing reviewer would), not
the interactive CLI prompt. These tests confirm the tool's logic is
correct; they are not, and do not claim to be, real labeled data.

## Labels that exist (updated 2026-10-07)

`labeling/labels/` holds 3 **partial** labels (6 contact frames in total) the
owner stated by frame number for three side-view practice clips. They were not
produced with `label_shots.py`; provenance is in each `.meta.json`
(`"complete": false`, `source: owner_stated_frame_numbers`). A frame not
listed is unlabeled, not a negative. The frame-numbering base (0 or 1) the
owner used was not stated. The videos themselves are not in the repo.

**Frame numbering warning (2026-10-07):** a media player's frame counter can
differ from OpenCV's decoder by several frames on variable-frame-rate mp4s
(about 3 frames on the Fed 1 clip). Always label on stills with the decoder's
frame number burned in, as `label_shots.py extract` produces. Each label's meta
now records `frame_numbering`.
