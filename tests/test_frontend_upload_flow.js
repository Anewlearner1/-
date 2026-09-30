// Node test-runner unit tests for frontend/upload_flow.js's pure
// classifyUploadResponse() function. No DOM, no network — hand-built
// fixtures shaped exactly like backend/api/upload.py's two response
// shapes (see that file's module docstring).
//
// Run with: node --test tests/test_frontend_upload_flow.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { SCREEN, classifyUploadResponse, errorCopyForCode } = require(
  path.join(__dirname, "..", "frontend", "upload_flow.js")
);

test("passed=true with a non-empty messages_zh (today: court_corners stub) -> INFO_CONFIRM", () => {
  const body = {
    video_path: "/tmp/good.mp4",
    passed: true,
    checks: [
      { name: "fps", status: "pass", message_zh: null, detail: "", metrics: {} },
      { name: "camera_stability", status: "pass", message_zh: null, detail: "", metrics: {} },
      {
        name: "court_corners",
        status: "not_implemented",
        message_zh: "球場四角偵測功能尚未完成...",
        detail: "",
        metrics: {},
      },
    ],
    messages_zh: ["球場四角偵測功能尚未完成..."],
    upload_id: "stub-abc123",
  };
  const result = classifyUploadResponse(200, body);
  assert.equal(result.screen, SCREEN.INFO_CONFIRM);
  assert.deepEqual(result.messages, ["球場四角偵測功能尚未完成..."]);
  assert.equal(result.uploadId, "stub-abc123");
});

test("passed=true with empty messages_zh -> SUBMITTED (hypothetical future state once court_corners is real)", () => {
  const body = {
    passed: true,
    checks: [
      { name: "fps", status: "pass", message_zh: null },
      { name: "camera_stability", status: "pass", message_zh: null },
      { name: "court_corners", status: "pass", message_zh: null },
    ],
    messages_zh: [],
    upload_id: "stub-xyz",
  };
  const result = classifyUploadResponse(200, body);
  assert.equal(result.screen, SCREEN.SUBMITTED);
  assert.deepEqual(result.messages, []);
  assert.equal(result.uploadId, "stub-xyz");
});

test("passed=false (fps FAIL) -> RESHOOT, lists messages_zh verbatim, no upload_id", () => {
  const body = {
    passed: false,
    checks: [
      {
        name: "fps",
        status: "fail",
        message_zh: "偵測到影片幀率約 28 fps，低於建議的 60 fps。",
      },
      { name: "camera_stability", status: "pass", message_zh: null },
      { name: "court_corners", status: "not_implemented", message_zh: "球場四角..." },
    ],
    messages_zh: [
      "偵測到影片幀率約 28 fps，低於建議的 60 fps。",
      "球場四角...",
    ],
  };
  const result = classifyUploadResponse(200, body);
  assert.equal(result.screen, SCREEN.RESHOOT);
  assert.deepEqual(result.messages, body.messages_zh);
  assert.equal(result.uploadId, null);
});

test("422 invalid_video_file -> ERROR screen, mapped Chinese copy, not the RESHOOT copy", () => {
  const body = { error: "invalid_video_file", detail: "OpenCV could not open file" };
  const result = classifyUploadResponse(422, body);
  assert.equal(result.screen, SCREEN.ERROR);
  assert.equal(result.errorCode, "invalid_video_file");
  assert.equal(result.messages.length, 1);
  assert.equal(result.messages[0], errorCopyForCode("invalid_video_file"));
  // Must not be the raw English detail.
  assert.notEqual(result.messages[0], body.detail);
});

test("404 file_not_found -> ERROR screen with its own mapped copy", () => {
  const body = { error: "file_not_found", detail: "[Errno 2] ..." };
  const result = classifyUploadResponse(404, body);
  assert.equal(result.screen, SCREEN.ERROR);
  assert.equal(result.errorCode, "file_not_found");
  assert.equal(result.messages[0], errorCopyForCode("file_not_found"));
});

test("unknown error code -> ERROR screen with default fallback copy", () => {
  const body = { error: "some_future_error_code", detail: "..." };
  const result = classifyUploadResponse(500, body);
  assert.equal(result.screen, SCREEN.ERROR);
  assert.equal(result.errorCode, "some_future_error_code");
  assert.equal(result.messages[0], errorCopyForCode("some_future_error_code"));
});

test("network-level failure (httpStatus 0, synthetic error body) -> ERROR, not a crash", () => {
  const result = classifyUploadResponse(0, { error: "network_error", detail: "fetch failed" });
  assert.equal(result.screen, SCREEN.ERROR);
});
