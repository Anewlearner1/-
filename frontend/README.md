# Frontend — upload + quality-gate flow, M6 dashboard

Implements `design/upload-flow.md` §1-§4 against the real
`POST /upload` endpoint in `backend/api/upload.py`. No build toolchain:
plain HTML/CSS/vanilla JS, loaded straight as `<script>` tags.

## Files

- `upload_flow.js` — pure, DOM-free state-classification logic.
  `classifyUploadResponse(httpStatus, body) -> {screen, messages, uploadId, errorCode}`.
  No fetch, no DOM — this is what's unit tested (see Tests below). Also
  where the error-code -> Traditional Chinese copy mapping lives, and
  `buildUploadFormData(file, racketHand)`, which builds the `POST /upload`
  body (adds `racket_hand` only for `"left"`/`"right"`).
- `upload.js` — DOM wiring: file input → `fetch('/upload')` → feeds the
  response into `classifyUploadResponse` → shows/hides the matching
  `<section>` in `upload.html` and renders `messages_zh` / error copy.
- `upload.html` — the five screens as hidden/shown `<section>`s: §1
  guidance, §2 loading, §3a info-confirm card, §3a submitted, §3b
  re-shoot, §4 error.
- `classify_cli.js` — tiny stdin/stdout bridge so a Python test can drive
  `classifyUploadResponse` from real backend responses (see Tests).

## Running it

This is a static page with no server of its own. To try it against a
real backend:

```
cd rally-ai
pip install -r requirements.txt
uvicorn backend.api.upload:app --reload --port 8000
```

Then open `frontend/upload.html` directly in a browser (e.g.
`python3 -m http.server 8080` from `frontend/` and visit
`http://localhost:8080/upload.html`), and if the page isn't served from
the same origin as the API, set `window.RALLY_API_BASE = "http://localhost:8000"`
before `upload.js` loads (e.g. add a `<script>window.RALLY_API_BASE = "...";</script>`
line in `upload.html` above the two script tags) — the backend has no
CORS headers configured, so cross-origin will need that added too if you
actually drive it from a browser this way. This repo has no browser
available to visually verify the page; it has only been driven through
its logic layer (see Tests).

## What's built vs. stubbed

**Built and tested (logic layer):**
- Screen selection for all three real API response shapes: passed=true
  with `messages_zh` non-empty (§3a info card — today's actual state,
  since `court_corners` is always `not_implemented`), passed=false (§3b
  re-shoot), and the 422 malformed-file error envelope (§4).
- The `messages_zh` array is rendered verbatim, in the backend's fixed
  order, never rewritten/translated/truncated (per design doc's explicit
  instruction not to touch the camera_stability disclaimer text).
- The pre-upload guidance checklist (§1) text, taken directly from the
  design doc.
- The racket-hand picker (§1.1): a `<fieldset>`/`<legend>` of three native
  radios, 右手 (`right`) / 左手 (`left`) / 不確定 (`""`, checked by
  default), above the upload button. 不確定 sends no `racket_hand` field,
  so the backend stores "not given" and forehand/backhand stays unanalysed
  rather than guessed. The choice is kept when the user goes back to
  re-upload. Unit tested via `buildUploadFormData`, and each value is
  posted to the real endpoint in the Python test; the radios themselves
  have not been tried in a browser (none here).

**Stubbed / not implemented:**
- `passed=true` with an **empty** `messages_zh` (straight to "已送出處理"
  with no info card) — the logic handles this (`SCREEN.SUBMITTED`) and is
  unit tested with a synthetic fixture, but it cannot happen against
  today's real backend since `court_corners` always contributes a
  `not_implemented` message. This will become reachable once
  cv-engineer's real court-corner checker ships (see `backend/README.md`).
- Upload progress percentage — design doc §2 says the upload step itself
  just needs *a* progress indicator and the quality-gate step is a short
  spinner; this shows a spinner for the whole request rather than a real
  byte-progress bar (no chunked-upload / `XMLHttpRequest.upload.onprogress`
  wiring). Fine for typical clip sizes but would need revisiting for very
  large files.
- The dashboard screen the design doc says to continue into after §3a
  (`dashboard.md`'s empty/loading state) is out of scope here — this flow
  stops at "已送出處理" with the stub `upload_id` shown.
- No drag-and-drop target, only a file picker button — §1's spec doesn't
  require drag-and-drop specifically.
- Manual testing here has only been done with the same synthetic
  fixture-video generator the backend tests use (`tests/synth.py`), via
  the Node/Python test bridge below — not against a hand-shot real phone
  video, and not visually in an actual browser (no browser in this
  sandbox).

## Error-code → Traditional Chinese copy mapping

Backend-engineer's note in `backend/api/upload.py` / `backend/README.md`
flagged that design doc §4 only shows an *example* Chinese string, while
the real API returns `{"error": <code>, "detail": <English>}` at
non-200. This mapping (defined in `frontend/upload_flow.js`,
`ERROR_COPY_ZH`) is what frontend built to close that gap, from reading
every `except` branch the real endpoint has today:

| `error` code | When the API actually returns it | Traditional Chinese copy shown |
|---|---|---|
| `invalid_video_file` | `check_upload_quality()` raises `ValueError` (OpenCV can't open the file / not a real video container) — HTTP 422 | 檔案格式不支援，請確認為常見影片格式後重新上傳。 |
| `file_not_found` | Defensive-only: the file the endpoint itself just wrote to disk went missing before it could be read back — HTTP 404 | 伺服器未能讀取您剛上傳的檔案，這通常是暫時性問題，與影片品質無關，請重新上傳一次；若持續發生請聯絡我們。 |
| `upload_not_found` | `GET /uploads/{id}` or `GET /uploads/{id}/shots` with an id that has no record (stale/mistyped link, or never created: failed-gate uploads create no row) — HTTP 404. **Not reachable from the current upload screen** (it only calls `POST /upload`); mapping is in place for the dashboard (M6) callers, not wired to any screen today | 找不到這筆上傳紀錄，可能是連結有誤或已失效，與影片品質無關；請回到上傳頁重新上傳影片。 |
| `invalid_racket_hand` | `POST /upload` optional form field `racket_hand` is not `left`/`right` (empty = not given) — HTTP 422, file not stored. **Reachable only if some other client sends a bad value**: the upload screen's racket-hand picker sends only `right`/`left`, or no field for 不確定 (`buildUploadFormData`) | 持拍手設定無效，請重新選擇「右手」、「左手」或「不確定」後再上傳；這與影片品質無關。 |
| *(anything else / missing)* | Any future/unrecognized error code, or a non-200 response that isn't even `{"error": ...}`-shaped (e.g. a network failure) | 發生未預期的錯誤，請稍後再試一次；若持續發生請聯絡我們。 |

All five use the §4 "problem with the file itself" visual language
(same `.card.fail` style, ⚠ marker), which is deliberately **not** the
§3b re-shoot screen's styling even though both use a red-ish card — the
re-shoot screen is reserved for an actual `passed:false` `QualityReport`.

**Reviewed by ui-ux-designer (2026-10).** This table — and the
authoritative version in `design/upload-flow.md` §4, which now takes
precedence for any future error code — is the final decision, not a
placeholder:
- `invalid_video_file`: kept frontend-engineer's string as-is. It already
  names the specific cause (unsupported/unreadable format) and a concrete
  next step, consistent with the backend's `message_zh` voice.
- `file_not_found`: rewritten. The original "上傳過程發生問題，請重新嘗試
  上傳一次" was too close to a generic catch-all and didn't make clear
  this is a server-side glitch unrelated to the user's footage — risking
  confusion with the §3b re-shoot screen. New copy says explicitly this is
  a server-side read failure, unrelated to video quality.
- unknown/default: kept as-is. A true catch-all for an error we cannot
  identify should stay generic rather than invent a false specific cause.

## Tests

Two layers, both exercising the same `classifyUploadResponse` function
frontend actually ships (no parallel reimplementation):

1. **`tests/test_frontend_upload_flow.js`** — Node's built-in test runner
   (`node --test tests/test_frontend_upload_flow.js`), hand-built
   fixtures shaped like the backend's documented response shapes, plus
   `buildUploadFormData`'s racket_hand rule. Fast, no backend needed.
   11 tests.

2. **`tests/test_frontend_against_backend.py`** — a pytest test (same
   `FastAPI TestClient` pattern as `tests/test_upload_api.py`) that
   drives three *real* requests through the real `/upload` endpoint
   (steady 60fps video → pass, 30fps video → FAIL, garbage bytes →
   malformed-file error, plus a real `GET /uploads/<nonexistent id>` 404 against a per-test temp DB), then pipes each real `(status, body)` pair into
   `frontend/upload_flow.js`'s actual `classifyUploadResponse` via a
   Node subprocess bridge (`frontend/classify_cli.js`) and asserts the
   screen matches design doc §3a/§3b/§4. This is the test that proves the
   frontend logic and the real backend actually agree, not two specs
   drifting independently. Also posts each racket-hand picker value, as
   built by the real `buildUploadFormData`, to the real `/upload` and
   checks it is accepted and stored (不確定 -> null). 8 tests (5
   functions, two parametrized). Skips gracefully if Node isn't on
   `PATH`.

Run everything:

```
cd rally-ai
node --test tests/test_frontend_upload_flow.js
python3 -m pytest -q
```

---

# Dashboard (M6) — `dashboard.html?upload=<id>`

Implements `design/dashboard.md` §1-§5 against the real `GET /uploads/{id}`,
`GET /uploads/{id}/shots` and `GET /uploads/{id}/video`. Same style as the
upload page: plain HTML/CSS/JS, no build step, no CDN.

## Files

- `dashboard_logic.js` — pure, DOM-free: `classifyUploadStatus` (queued /
  processing -> loading + poll every 3 s, done -> fetch shots, failed ->
  error), `buildShotsView` (0 shots -> empty state, else sorted cards +
  summary), `currentShotIndex` (§4.1 highlight rule), `fhBhPill` (§3.1),
  `ballSpeedPill` (§5, always 尚未分析), `summarize`, `shouldAutoScroll`.
  Error copy comes from `upload_flow.js` `errorCopyForCode` / `ERROR_COPY_ZH`
  (require()d under Node, `window.UploadFlow` in the browser), not copied.
- `dashboard.js` — DOM wiring: fetch, polling, rendering, video sync.
- `dashboard.html` — the page; loads `upload_flow.js`, `dashboard_logic.js`,
  `dashboard.js` in that order.

Open it like the upload page (static server + `RALLY_API_BASE` if the API is
on another origin), e.g. `http://localhost:8080/dashboard.html?upload=<id>`.

## Behaviour and decisions

- **Highlight (§4.1/§4.2)**: last shot with `contact_time_s <= currentTime`,
  none before the first shot. Updated on `timeupdate`, `seeking` (fires while
  the seek bar is dragged, §4.5) and `seeked`; only re-rendered when the
  index changes; CSS transitions fade the highlight (§4.3).
- **Click to seek (§4.4)**: seeks to exactly `contact_time_s`, **no 0.5-1 s
  pre-roll** (the doc makes it optional). With a pre-roll the §4.1 rule would
  highlight the *previous* shot on the next `timeupdate`, contradicting
  "立即高亮該拍". The page passes a 1 ms tolerance (`SEEK_TOLERANCE_S`) to the
  rule so a seek that a browser reports a hair early does not flip back; this
  is well under one frame, so playback highlighting is unaffected.
- **Auto-scroll (§4.6)**: the list scrolls to the current card unless the user
  scrolled / swiped / clicked / arrow-keyed in the list in the last 3 s. A
  click on a shot always scrolls to it.
- **FH/BH (§3.1)**: driven only by `fh_bh_status`. `not_analyzed` (or missing /
  unknown) -> 尚未分析; `undetermined` -> 無法判斷 + tooltip; `labeled` ->
  正手 / 反手, with 推測： and a dashed secondary pill when confidence < 0.5.
  A labeled shot with **null** confidence is also shown as 推測 (not stated in
  the doc; chosen so an unknown confidence never looks certain). `other` ->
  不適用 (ADR 0002 wording), grey.
- **Summary**: counts only `labeled` forehand/backhand; `other` is not counted.
  No labeled shot -> 尚未分析 (never 0). Exception: if the classifier ran and
  abstained on **every** shot, the summary says 無法判斷 instead, following
  §3.1's rule that undetermined must not read as 尚未分析. If only `other`
  shots are labeled the counts are a real 0 / 0 (the classifier did run).
  With any labels, both counts get an 實驗中 tag and a "其中 N 拍為推測" note.
  Summary rows keep §1's 正拍 / 反拍 wording; pills use §3.1's 正手 / 反手.
- **Ball speed (§5)**: always 尚未分析. `peak_speed` (wrist speed) is never read
  by the page; `ball_speed_kmh` is ignored even if non-null until M5 defines
  its display.
- **States (§2)**: loading shows a skeleton plus 分析中 and the raw video (it is
  playable before analysis ends); 0 shots shows the doc's empty copy with a
  重新上傳 link; `failed` shows a Chinese error with the worker's English
  message only under 技術細節, plus a link back to `upload.html` (the
  quality-gate re-shoot screen is not used: a worker failure is not a footage
  problem the gate found). 404 / missing `?upload=` -> the `upload_not_found`
  copy; network errors -> the default copy.
- **Accessibility**: the shot list is an `<ol>` of buttons with a roving
  tabindex (one Tab stop; arrows / Home / End move, Enter / Space seek); the
  current card has `aria-current="true"`. A polite live region announces the
  current shot after a click or while paused (not during playback, to avoid
  chatter). The timeline is a visual duplicate of the list and is
  `aria-hidden`. Narrow screens get a 收合 / 展開逐拍清單 toggle.

## Not done / not verified

- **F1 skeleton overlay**: no API serves an overlay video or landmarks, so the
  page plays the raw upload.
- `upload.html` does not link to the dashboard after "已送出處理" (that file is
  outside this change); open the dashboard URL by hand with the upload id.
- **Not run in a real browser** (none here). Verified through the logic tests,
  a fake-DOM run of `dashboard.js`, and real backend responses. Not checked:
  how it looks, real `timeupdate` / `seeking` timing during a drag, scroll
  maths, smooth transitions, real screen-reader output.

## Dashboard tests

- `tests/test_frontend_dashboard_logic.js` — highlight rule incl. boundaries,
  pills for every status, summary counts, states, copy reuse.
- `tests/test_frontend_dashboard_page.js` — static checks: every id the script
  uses exists, script order, no external URLs, no duplicated error copy.
- `tests/test_frontend_dashboard_dom.js` — runs the real `dashboard.js` against
  a fake DOM + mocked fetch: polling, aria-current following playback / seek
  drag / click, 404, empty state.
- `tests/test_frontend_dashboard_against_backend.py` — real TestClient
  responses (queued, processing, failed, 0 shots, unclassified, classified,
  all-undetermined, 404, Range video) piped into `dashboard_logic.js` via Node.

Note: on Node 22, `node --test tests/` fails (it treats the directory as a
module). Use `node --test tests/*.js`.
