# Backend — upload quality gate (M0)

Status: M0 prep work, done ahead of M1 per product-manager's request. See
`docs/technical-plan.md` §5 for the input-spec requirements this
implements, and §6 for milestones.

## What this is

`upload_quality.py` is the quality gate that should run right after a
video is uploaded and *before* it's queued into the batch-processing
pipeline (§2: offline batch, upload → background compute → output). It's a
standalone module with no batch-processing job queue / GPU scheduler
wiring yet (that's separate, still-to-be-built backend-engineer work) —
but it now has a thin JSON API wrapper (below): `check_upload_quality()`
is the function that wrapper calls first.

## Upload API endpoint

`backend/api/upload.py` is a small FastAPI app exposing
`POST /upload` (multipart file upload). It saves the file under
`data/uploads/`, runs `check_upload_quality()`, and returns the
`QualityReport` as JSON with the *exact* field names ui-ux-designer's
`design/upload-flow.md` already documents against this module
(`passed`, `checks[].name/status/message_zh/detail/metrics`,
`messages_zh`) — that doc is the spec for what the frontend does with
each field/status value; read it before changing this response shape.

- `passed: true` responses also include `upload_id`: the id of a real
  SQLite `uploads` row (`backend/db.py`) with status `queued`.
  **The queue is consumed only by `python -m backend.worker`, which nothing
  starts automatically; with no worker running, status stays `queued`.** Failed-gate
  uploads create no row (the report is returned; client must re-shoot).
- `GET /uploads/{upload_id}` -> status + stored quality report.
- `GET /uploads/{upload_id}/shots` -> `{upload_id, shot_count, shots[]}`
  ordered by `shot_index`; each shot has `contact_frame`, `contact_time_s`,
  `peak_speed` (wrist speed, not ball speed), `wrist`, `fh_bh_label`,
  `ball_speed_kmh`, `source`. `fh_bh_label` / `ball_speed_kmh` are JSON
  `null` (= dashboard "尚未分析", never 0) until M3 / M5 exist. Nothing
  fills `shots` in production yet; `db.insert_shots_from_result()` is for a
  future worker (tested with `ml.shot_timing.detect_shots`).
- Unknown id -> 404 `{"error": "upload_not_found", "detail": ...}`.
- DB: env `RALLY_DB_PATH`, default `data/rally.sqlite3`. DB column is
  `stroke_label`; API key is `fh_bh_label` per design/dashboard.md.
- A malformed/unreadable file (`ValueError` from `check_upload_quality`)
  returns HTTP 422 with a *different* JSON shape
  (`{"error": "invalid_video_file", "detail": ...}`), not a `QualityReport`
  — see design doc §4 on why the frontend needs to tell these apart.
- Tests: `tests/test_upload_api.py`, using FastAPI's `TestClient` (no
  server process needed) and the same synthetic-video fixtures as
  `test_upload_quality.py`.

## What's real vs. placeholder

| Check | Status | Notes |
|---|---|---|
| FPS >= 60 | **Real** | Reads `CAP_PROP_FPS` from the container via OpenCV. Below 60fps -> FAIL with a re-shoot message citing the ~1m/frame ball blur from §5. |
| Camera stability (fixed/tripod) | **Real, but a heuristic** | Estimates frame-to-frame global displacement via `cv2.phaseCorrelate` on a sampled subset of frames, and (as of the Finding 1 fix, see `docs/qa-findings.md`) also tracks the cumulative, directionally-consistent drift across the sampled sequence so a slow, smooth pan is now flagged too, not just high-frequency jitter. This is still a cheap heuristic screen, **not** a certified tripod detector: it can be fooled by a very steady handheld shot, a large subject near the lens, or a pan that reverses direction partway through the clip (the directional drift partially cancels). Thresholds (`MAX_JITTER_PIXELS`, `MAX_JITTER_STD_PIXELS`, `MAX_CUMULATIVE_DRIFT_PIXELS`, `MIN_PAN_DIRECTIONAL_CONSISTENCY`) are hand-picked, not calibrated against labeled footage. Full honesty notes are in the module docstring above `_estimate_camera_jitter`. |
| Court corners visible (all 4) | **Placeholder / stub only** | This is explicitly **cv-engineer's** work: court keypoint detection + homography, milestone M4 in `docs/technical-plan.md` §6, which doesn't exist yet. `upload_quality.py` defines a `CourtCornerChecker` abstract interface and ships `NotImplementedCourtCornerChecker`, which always returns `CheckStatus.NOT_IMPLEMENTED` and a Chinese message telling the uploader to self-check the framing. **It never returns a fake PASS.** |

## Dependency on cv-engineer (M4)

Once cv-engineer's court-corner detector exists, wire it in by
implementing `CourtCornerChecker.check()` and passing that instance as
`court_corner_checker=` to `check_upload_quality()` — no caller-side
changes needed elsewhere (upload handler, queue, etc.), since the
interface and the `QualityReport.court_corners` field don't change. Until
that lands:

- `QualityReport.passed` treats `NOT_IMPLEMENTED` as non-blocking (an
  upload isn't stuck forever waiting on unbuilt CV work), but a real
  `FAIL` from a plugged-in checker *does* block, same as any other check.
- The frontend should still show the stub's `message_zh` so the uploader
  self-checks framing manually in the meantime.

## Result shape

`check_upload_quality(video_path, court_corner_checker=None) -> QualityReport`

- `QualityReport.passed`: bool, safe to enqueue.
- `QualityReport.checks`: `[fps, camera_stability, court_corners]`, each a
  `CheckResult(name, status, detail, message_zh, metrics)`.
- `QualityReport.messages_zh`: ordered list of Traditional Chinese
  re-shoot prompts for every check that isn't a clean PASS — meant to be
  surfaced directly by frontend-engineer's upload flow.

## Tests

`tests/test_upload_quality.py` + `tests/synth.py` (synthetic mp4
generation via `cv2.VideoWriter`, following the *pattern* used in the
sibling `tennis-form-coach` project's `tests/test_cli.py` — no code or
domain logic shared between the two projects). Covers: fps pass/fail,
camera-stability pass/fail (static/textured vs. simulated jitter), the
stub court-corner check never passing, pluggability of a real checker,
and that a not-implemented court check alone doesn't block upload.

Run with:

```
cd rally-ai
pip install -r requirements.txt
python3 -m pytest -q
```

9/9 tests pass as of this writing (skips gracefully to a Chinese-language
message if the environment lacks an mp4 encoder). Plus 3 more in
`tests/test_upload_api.py` covering the API endpoint (pass, fps FAIL,
malformed file) — 29/29 total across the repo as of this writing.

## 影片資料夾（`backend/library.py`）

後端直接讀取的影片放在 `data/videos/`（可用環境變數 `RALLY_VIDEO_DIR` 改路徑）。`data/` 已列入 `.gitignore`：影片含可辨識的人物，而本專案是 AGPL 公開 repo，**影片不得 commit**。

```bash
python -m backend.library ingest 影片.mp4 [...]   # 複製進資料夾（同名不覆蓋，檔名會清成 A-Za-z0-9._-）
python -m backend.library scan                    # 讀取資訊 + 上傳品質檢查，輸出 JSON
python -m backend.library scan --shots            # 另跑姿態與擊球偵測（慢，首次會下載模型）
```

- `resolve_video(name)` 只接受純檔名，拒絕 `..` 與路徑分隔，供之後 API 以名稱取檔時使用。
- 資料夾在執行環境的本機磁碟上；沙盒容器被回收就會消失，原始檔請自行保留。
- `scan --shots` 的擊球數只是手腕速度峰值，**未經真人標註驗證**，會有偽陽性。


## Worker (`backend/worker.py`)

```bash
python -m backend.worker --once                 # process everything queued, then exit
python -m backend.worker                        # poll every 2 s until Ctrl-C
python -m backend.worker --ball-filter --merge-within-s 0.5
python -m backend.worker --requeue-processing   # after a crash, if no other worker is running
```

Claims the oldest `queued` upload atomically (`BEGIN IMMEDIATE`), sets `processing`, runs
`cv.pose_overlay.extract_player_landmarks` + `ml.shot_timing.detect_shots` (optionally the
ball filter and duplicate merge), replaces that upload's `shots`, sets `done`; any exception
sets `failed` and stores the message in `uploads.error` (returned by `GET /uploads/{id}` as
`error`). Not done: forehand/backhand labels (`fh_bh_label` stays null; the classifier has no
real validation), ball speed, retries, GPU routing, scheduling, billing. Run on a real video
(Fons practice, queued directly past the 60 fps gate): `done`, 8 shots with merge 0.5 s, in
the order and frames of the earlier standalone run.
