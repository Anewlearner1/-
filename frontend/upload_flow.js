/**
 * upload_flow.js — pure, DOM-free state-classification logic for the
 * upload + quality-gate flow described in design/upload-flow.md.
 *
 * This module has no DOM/fetch dependency on purpose so it can be unit
 * tested under plain Node (see tests/test_frontend_upload_flow.js and
 * tests/test_frontend_against_backend.py) without a browser.
 *
 * ---------------------------------------------------------------------
 * ERROR-CODE -> TRADITIONAL CHINESE COPY MAPPING (frontend-engineer's gap
 * to fill, per backend-engineer's note in backend/api/upload.py and
 * backend/README.md): the design doc (design/upload-flow.md §4) only
 * gives an *example* re-shoot-style Chinese string for the malformed-file
 * case, but the real API returns an English `detail` inside
 * `{"error": <code>, "detail": <english>}` at HTTP 422 (and a 404 shape
 * for the defensive file_not_found branch). We must not show the English
 * `detail` to end users, and must not reuse the §3b "re-shoot" visual
 * language for it (§4 explicitly says this is a *different* kind of
 * problem: broken file vs. bad footage). Mapping built by reading the
 * actual `except` branches in backend/api/upload.py:
 *
 *   "invalid_video_file" -> shown when check_upload_quality() raises
 *     ValueError (OpenCV couldn't open the file / not a real video
 *     container). Copy per design doc §4's own suggested wording:
 *     "檔案格式不支援，請確認為常見影片格式後重新上傳。"
 *
 *   "file_not_found" -> defensive-only branch (the file we ourselves just
 *     wrote to disk went missing before we could read it back); not
 *     expected in practice, but the API can return it, so we still need
 *     user-facing copy rather than silently failing. ui-ux-designer
 *     reviewed this one (2026-10) and replaced the original generic
 *     "上傳過程發生問題" string: it read too close to a vague catch-all and
 *     didn't make clear this is a server-side glitch, not a problem with
 *     the user's footage or file. Final copy, codified in
 *     design/upload-flow.md §4:
 *     "伺服器未能讀取您剛上傳的檔案，這通常是暫時性問題，與影片品質無關，
 *      請重新上傳一次；若持續發生請聯絡我們。"
 *
 *   (default/unknown code, e.g. a future error we don't yet know about) ->
 *     ui-ux-designer reviewed and kept frontend-engineer's string as-is
 *     (design/upload-flow.md §4): a true catch-all for an error we cannot
 *     describe should stay generic rather than invent a false specific
 *     cause.
 *     "發生未預期的錯誤，請稍後再試一次；若持續發生請聯絡我們。"
 * ---------------------------------------------------------------------
 */

/**
 * "upload_not_found" (HTTP 404, GET /uploads/{id} and
 * GET /uploads/{id}/shots): the id asked about has no record (stale or
 * mistyped link, or an id never created -- failed-gate uploads create no
 * row). REACHABILITY: NOT reachable from the current upload screen, which
 * only calls POST /upload; the mapping exists for the dashboard (M6) GET
 * callers. Copy per design/upload-flow.md §4.1.
 */
const ERROR_COPY_ZH = {
  invalid_video_file: "檔案格式不支援，請確認為常見影片格式後重新上傳。",
  file_not_found:
    "伺服器未能讀取您剛上傳的檔案，這通常是暫時性問題，與影片品質無關，請重新上傳一次；若持續發生請聯絡我們。",
  upload_not_found:
    "找不到這筆上傳紀錄，可能是連結有誤或已失效，與影片品質無關；請回到上傳頁重新上傳影片。",
};

const DEFAULT_ERROR_COPY_ZH =
  "發生未預期的錯誤，請稍後再試一次；若持續發生請聯絡我們。";

function errorCopyForCode(code) {
  return ERROR_COPY_ZH[code] || DEFAULT_ERROR_COPY_ZH;
}

/**
 * Screens this module can route to. Kept as plain string constants (not
 * DOM node ids) so this file has zero browser dependency.
 *
 *   GUIDANCE  — §1 pre-upload shooting guidance (also used as the
 *               "查看拍攝指引" destination from §3b, and shown before any
 *               response has come back).
 *   LOADING   — §2 upload-in-progress / "檢查影片品質中…" state.
 *   INFO_CONFIRM — §3a, passed === true AND messages_zh non-empty: the
 *               non-blocking info card (e.g. court_corners
 *               not_implemented) shown before continuing.
 *   SUBMITTED — §3a, passed === true AND messages_zh empty: go straight
 *               to "已送出處理" with no interstitial card.
 *   RESHOOT   — §3b, passed === false: blocking re-shoot screen listing
 *               messages_zh for every FAIL check.
 *   ERROR     — §4, malformed/unreadable file or any other non-QualityReport
 *               error envelope. Distinct visual language from RESHOOT.
 */
const SCREEN = Object.freeze({
  GUIDANCE: "guidance",
  LOADING: "loading",
  INFO_CONFIRM: "info_confirm",
  SUBMITTED: "submitted",
  RESHOOT: "reshoot",
  ERROR: "error",
});

/**
 * classifyUploadResponse(httpStatus, body) -> {
 *   screen: one of SCREEN.*,
 *   messages: string[]   // message_zh strings to display, already in the
 *                         // fixed backend order (fps -> camera_stability
 *                         // -> court_corners) for QualityReport shapes,
 *                         // or a single mapped error string for ERROR.
 *   uploadId: string | null,  // present only for INFO_CONFIRM / SUBMITTED
 *   errorCode: string | null, // present only for ERROR
 * }
 *
 * This function makes NO network calls and touches no DOM — it only looks
 * at the already-parsed (httpStatus, body) pair, exactly as the real
 * backend/api/upload.py can emit it (see its module docstring for the two
 * distinct response shapes: 200 QualityReport-shaped vs. 422/404
 * {"error", "detail"}-shaped).
 */
function classifyUploadResponse(httpStatus, body) {
  body = body || {};

  // §4: any non-200 response, or a 200 body that for some reason lacks the
  // QualityReport shape, is an "error" screen — never the §3b re-shoot
  // screen, which is reserved for a real passed:false QualityReport.
  const looksLikeQualityReport =
    httpStatus === 200 &&
    typeof body.passed === "boolean" &&
    Array.isArray(body.checks);

  if (!looksLikeQualityReport) {
    const errorCode = typeof body.error === "string" ? body.error : "unknown";
    return {
      screen: SCREEN.ERROR,
      messages: [errorCopyForCode(errorCode)],
      uploadId: null,
      errorCode,
    };
  }

  const messagesZh = Array.isArray(body.messages_zh) ? body.messages_zh : [];

  if (body.passed === false) {
    // §3b — blocking re-shoot screen, one bullet per FAIL check, original
    // message_zh text verbatim (design doc: "原文呈現，不重寫").
    return {
      screen: SCREEN.RESHOOT,
      messages: messagesZh,
      uploadId: null,
      errorCode: null,
    };
  }

  // passed === true
  if (messagesZh.length > 0) {
    // §3a, with a non-blocking info card first (today: always at least the
    // court_corners not_implemented message).
    return {
      screen: SCREEN.INFO_CONFIRM,
      messages: messagesZh,
      uploadId: body.upload_id || null,
      errorCode: null,
    };
  }

  // §3a, clean pass with nothing to show — straight to "已送出處理".
  return {
    screen: SCREEN.SUBMITTED,
    messages: [],
    uploadId: body.upload_id || null,
    errorCode: null,
  };
}

const api = {
  SCREEN,
  ERROR_COPY_ZH,
  DEFAULT_ERROR_COPY_ZH,
  errorCopyForCode,
  classifyUploadResponse,
};

// UMD-ish export: CommonJS (Node tests) or a plain browser global.
if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else if (typeof window !== "undefined") {
  window.UploadFlow = api;
}
