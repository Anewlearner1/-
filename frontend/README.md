# Frontend — upload + quality-gate flow

Implements `design/upload-flow.md` §1-§4 against the real
`POST /upload` endpoint in `backend/api/upload.py`. No build toolchain:
plain HTML/CSS/vanilla JS, loaded straight as `<script>` tags.

## Files

- `upload_flow.js` — pure, DOM-free state-classification logic.
  `classifyUploadResponse(httpStatus, body) -> {screen, messages, uploadId, errorCode}`.
  No fetch, no DOM — this is what's unit tested (see Tests below). Also
  where the error-code -> Traditional Chinese copy mapping lives.
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
| *(anything else / missing)* | Any future/unrecognized error code, or a non-200 response that isn't even `{"error": ...}`-shaped (e.g. a network failure) | 發生未預期的錯誤，請稍後再試一次；若持續發生請聯絡我們。 |

All three use the §4 "problem with the file itself" visual language
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
   fixtures shaped like the backend's documented response shapes. Fast,
   no backend needed. 7 tests.

2. **`tests/test_frontend_against_backend.py`** — a pytest test (same
   `FastAPI TestClient` pattern as `tests/test_upload_api.py`) that
   drives three *real* requests through the real `/upload` endpoint
   (steady 60fps video → pass, 30fps video → FAIL, garbage bytes →
   malformed-file error), then pipes each real `(status, body)` pair into
   `frontend/upload_flow.js`'s actual `classifyUploadResponse` via a
   Node subprocess bridge (`frontend/classify_cli.js`) and asserts the
   screen matches design doc §3a/§3b/§4. This is the test that proves the
   frontend logic and the real backend actually agree, not two specs
   drifting independently. 3 tests. Skips gracefully if Node isn't on
   `PATH`.

Run everything:

```
cd rally-ai
node --test tests/test_frontend_upload_flow.js
python3 -m pytest -q
```
