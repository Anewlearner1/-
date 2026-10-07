// Node test-runner unit tests for frontend/dashboard_logic.js (design/dashboard.md).
// Run with: node --test tests/test_frontend_dashboard_logic.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const L = require(path.join(__dirname, "..", "frontend", "dashboard_logic.js"));
const { ERROR_COPY_ZH, DEFAULT_ERROR_COPY_ZH } = require(
  path.join(__dirname, "..", "frontend", "upload_flow.js")
);

function shot(i, t, extra = {}) {
  return {
    shot_index: i,
    contact_frame: Math.round(t * 60),
    contact_time_s: t,
    peak_speed: 123.4,
    wrist: "right",
    fh_bh_label: null,
    fh_bh_status: "not_analyzed",
    fh_bh_confidence: null,
    ball_speed_kmh: null,
    source: "ml.shot_timing.detect_shots",
    ...extra,
  };
}

const SHOTS = [shot(0, 1.0), shot(1, 2.5), shot(2, 4.0)];

// ---------------------------------------------------------------- §4 highlight
test("§4.2: before the first shot nothing is highlighted", () => {
  assert.equal(L.currentShotIndex(SHOTS, 0), -1);
  assert.equal(L.currentShotIndex(SHOTS, 0.999), -1);
});

test("§4.1: exactly at a contact time that shot is highlighted (<=)", () => {
  assert.equal(L.currentShotIndex(SHOTS, 1.0), 0);
  assert.equal(L.currentShotIndex(SHOTS, 2.5), 1);
  assert.equal(L.currentShotIndex(SHOTS, 4.0), 2);
});

test("§4.1: between two shots the PREVIOUS one stays highlighted, not the nearest", () => {
  assert.equal(L.currentShotIndex(SHOTS, 2.49), 0); // nearer to shot 1, still shot 0
  assert.equal(L.currentShotIndex(SHOTS, 2.51), 1);
  assert.equal(L.currentShotIndex(SHOTS, 3.99), 1);
});

test("§4.1: after the last shot the last one stays highlighted", () => {
  assert.equal(L.currentShotIndex(SHOTS, 4.0001), 2);
  assert.equal(L.currentShotIndex(SHOTS, 9999), 2);
});

test("highlight: no shots / NaN time -> -1", () => {
  assert.equal(L.currentShotIndex([], 5), -1);
  assert.equal(L.currentShotIndex(SHOTS, NaN), -1);
  assert.equal(L.currentShotIndex(null, 5), -1);
});

test("highlight: tolerance absorbs seek rounding only, never a whole frame", () => {
  // strict default
  assert.equal(L.currentShotIndex(SHOTS, 2.4995), 0);
  // the page's tolerance: a seek landing 0.5 ms early still highlights the clicked shot
  assert.equal(L.currentShotIndex(SHOTS, 2.4995, L.SEEK_TOLERANCE_S), 1);
  // one 60 fps frame early is NOT absorbed
  assert.equal(L.currentShotIndex(SHOTS, 2.5 - 1 / 60, L.SEEK_TOLERANCE_S), 0);
  assert.ok(L.SEEK_TOLERANCE_S < 1 / 120);
});

test("§4.4: click-to-seek target equals contact_time_s, so it highlights that shot", () => {
  SHOTS.forEach((s, i) => {
    assert.equal(L.seekTimeFor(s), s.contact_time_s);
    assert.equal(L.currentShotIndex(SHOTS, L.seekTimeFor(s)), i);
  });
});

test("§1: shots are ordered by contact_time_s; highlight works on unsorted API input", () => {
  const sorted = L.sortShots([shot(2, 4.0), shot(0, 1.0), shot(1, 2.5)]);
  assert.deepEqual(sorted.map((s) => s.contact_time_s), [1.0, 2.5, 4.0]);
  assert.equal(L.currentShotIndex(sorted, 3), 1);
});

test("§4.6: auto-scroll pauses for 3 s after a manual scroll", () => {
  assert.equal(L.MANUAL_SCROLL_PAUSE_MS, 3000);
  assert.equal(L.shouldAutoScroll(10_000, null), true);
  assert.equal(L.shouldAutoScroll(10_000, 9_000), false);
  assert.equal(L.shouldAutoScroll(10_000, 7_001), false);
  assert.equal(L.shouldAutoScroll(10_000, 7_000), true);
});

// ---------------------------------------------------------------- §3.1 pills
test("§3.1 not_analyzed -> grey 尚未分析", () => {
  const p = L.fhBhPill(shot(0, 1));
  assert.equal(p.text, "尚未分析");
  assert.equal(p.tone, L.TONE.PLACEHOLDER);
});

test("§3.1 missing / unknown status -> 尚未分析 (never guessed from fh_bh_label)", () => {
  assert.equal(L.fhBhPill({ contact_time_s: 1 }).text, "尚未分析");
  assert.equal(L.fhBhPill(shot(0, 1, { fh_bh_status: "weird", fh_bh_label: "forehand" })).text, "尚未分析");
});

test("§3.1 undetermined -> 無法判斷 with the tooltip, never 尚未分析", () => {
  const p = L.fhBhPill(shot(0, 1, { fh_bh_status: "undetermined" }));
  assert.equal(p.text, "無法判斷");
  assert.equal(p.tone, L.TONE.PLACEHOLDER);
  assert.equal(p.tooltip, "這一拍的畫面不足以判斷正反手");
});

test("§3.1 labeled with confidence >= 0.5 -> 正手 / 反手", () => {
  const fh = L.fhBhPill(shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "forehand", fh_bh_confidence: 0.9 }));
  const bh = L.fhBhPill(shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "backhand", fh_bh_confidence: 0.5 }));
  assert.equal(fh.text, "正手");
  assert.equal(fh.tone, L.TONE.VALUE);
  assert.equal(bh.text, "反手"); // 0.5 is not below 0.5
});

test("§3.1 labeled with confidence < 0.5 -> 推測： prefix, secondary style", () => {
  const p = L.fhBhPill(shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "backhand", fh_bh_confidence: 0.3 }));
  assert.equal(p.text, "推測：反手");
  assert.equal(p.tone, L.TONE.GUESS);
  const q = L.fhBhPill(shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "forehand", fh_bh_confidence: 0.4999 }));
  assert.equal(q.text, "推測：正手");
});

test("§3.1 labeled with null confidence is treated as a guess", () => {
  const p = L.fhBhPill(shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "forehand", fh_bh_confidence: null }));
  assert.equal(p.text, "推測：正手");
});

test("ADR 0002: labeled other -> 不適用 (grey)", () => {
  const p = L.fhBhPill(shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "other", fh_bh_confidence: 0.8 }));
  assert.equal(p.text, "不適用");
  assert.equal(p.tone, L.TONE.PLACEHOLDER);
});

test("§5: ball speed is always 尚未分析, never peak_speed or a number", () => {
  for (const s of [shot(0, 1), shot(0, 1, { ball_speed_kmh: 84.7 }), shot(0, 1, { peak_speed: 999 })]) {
    const p = L.ballSpeedPill(s);
    assert.equal(p.text, "尚未分析");
    assert.equal(p.tone, L.TONE.PLACEHOLDER);
    const card = JSON.stringify(L.shotCardModel(s, 0));
    assert.ok(!card.includes("999") && !card.includes("123.4") && !card.includes("84.7"), card);
  }
});

// ---------------------------------------------------------------- summary
test("summary: nothing labeled -> 尚未分析 for both, never 0; no 實驗中", () => {
  const s = L.summarize(SHOTS);
  assert.equal(s.shotCount, 3);
  assert.equal(s.forehand.text, "尚未分析");
  assert.equal(s.backhand.text, "尚未分析");
  assert.equal(s.forehand.count, null);
  assert.equal(s.experimental, false);
});

test("summary: 0 shots -> shot count 0 but FH/BH 尚未分析", () => {
  const s = L.summarize([]);
  assert.equal(s.shotCount, 0);
  assert.equal(s.forehand.text, "尚未分析");
});

test("summary: classifier abstained on every shot -> 無法判斷, not 尚未分析, not 0", () => {
  const s = L.summarize([shot(0, 1, { fh_bh_status: "undetermined" }), shot(1, 2, { fh_bh_status: "undetermined" })]);
  assert.equal(s.forehand.text, "無法判斷");
  assert.equal(s.backhand.count, null);
});

test("summary: counts only labeled forehand/backhand; other and undetermined not counted", () => {
  const s = L.summarize([
    shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "forehand", fh_bh_confidence: 0.9 }),
    shot(1, 2, { fh_bh_status: "labeled", fh_bh_label: "forehand", fh_bh_confidence: 0.3 }),
    shot(2, 3, { fh_bh_status: "labeled", fh_bh_label: "backhand", fh_bh_confidence: 0.8 }),
    shot(3, 4, { fh_bh_status: "labeled", fh_bh_label: "other", fh_bh_confidence: 0.9 }),
    shot(4, 5, { fh_bh_status: "undetermined" }),
  ]);
  assert.equal(s.shotCount, 5);
  assert.equal(s.forehand.count, 2);
  assert.equal(s.backhand.count, 1);
  assert.equal(s.forehand.text, "2");
  assert.equal(s.experimental, true);
  assert.equal(s.guessCount, 1);
});

test("summary: only `other` labeled -> real zeros (classifier did run), with 實驗中", () => {
  const s = L.summarize([shot(0, 1, { fh_bh_status: "labeled", fh_bh_label: "other", fh_bh_confidence: 0.9 })]);
  assert.equal(s.forehand.count, 0);
  assert.equal(s.backhand.count, 0);
  assert.equal(s.experimental, true);
});

// ---------------------------------------------------------------- states
test("status queued / processing -> loading + poll", () => {
  for (const status of ["queued", "processing"]) {
    const r = L.classifyUploadStatus(200, { upload_id: "x", status });
    assert.equal(r.state, L.STATE.LOADING);
    assert.equal(r.poll, true);
    assert.equal(r.messages[0], "分析中，完成後會自動顯示擊球資料");
  }
  assert.ok(L.POLL_INTERVAL_MS >= 2000 && L.POLL_INTERVAL_MS <= 5000);
});

test("status done -> ready; failed -> failed with Chinese copy, English only as detail", () => {
  assert.equal(L.classifyUploadStatus(200, { status: "done" }).state, L.STATE.READY);
  const f = L.classifyUploadStatus(200, { status: "failed", error: "RuntimeError: boom" });
  assert.equal(f.state, L.STATE.FAILED);
  assert.equal(f.poll, false);
  assert.ok(!f.messages[0].includes("boom"));
  assert.equal(f.technicalDetail, "RuntimeError: boom");
});

test("404 upload_not_found reuses ERROR_COPY_ZH from upload_flow.js", () => {
  const body = { error: "upload_not_found", detail: "no upload with id 'x'" };
  for (const r of [L.classifyUploadStatus(404, body), L.buildShotsView(404, body)]) {
    assert.equal(r.state, L.STATE.ERROR);
    assert.equal(r.messages[0], ERROR_COPY_ZH.upload_not_found);
  }
});

test("network error / unknown status -> default error copy", () => {
  assert.equal(L.classifyUploadStatus(0, { error: "network_error" }).messages[0], DEFAULT_ERROR_COPY_ZH);
  assert.equal(L.classifyUploadStatus(200, { status: "zzz" }).messages[0], DEFAULT_ERROR_COPY_ZH);
});

test("§2: 0 shots -> empty state with the design copy, not an empty list", () => {
  const v = L.buildShotsView(200, { upload_id: "x", shot_count: 0, shots: [] });
  assert.equal(v.state, L.STATE.EMPTY);
  assert.equal(v.messages[0], "這段影片沒有偵測到擊球，可能是影片太短或球員動作不明顯");
});

test("normal view: sorted cards titled 第N拍 with mm:ss", () => {
  const v = L.buildShotsView(200, { upload_id: "x", shot_count: 2, shots: [shot(1, 75.9), shot(0, 14.2)] });
  assert.equal(v.state, L.STATE.NORMAL);
  assert.deepEqual(v.cards.map((c) => [c.title, c.timeText]), [["第1拍", "00:14"], ["第2拍", "01:15"]]);
  assert.equal(v.summary.shotCount, 2);
});

test("parseUploadId / formatTime", () => {
  assert.equal(L.parseUploadId("?upload=abc123"), "abc123");
  assert.equal(L.parseUploadId("?upload=%20"), null);
  assert.equal(L.parseUploadId(""), null);
  assert.equal(L.formatTime(0), "00:00");
  assert.equal(L.formatTime(190.9), "03:10");
  assert.equal(L.formatTime(3725), "1:02:05");
});

test("timelinePercent stays within 0-100 with or without a duration", () => {
  assert.equal(L.timelinePercent(5, 10, SHOTS), 50);
  const p = L.timelinePercent(4.0, NaN, SHOTS);
  assert.ok(p > 0 && p < 100);
  assert.equal(L.timelinePercent(20, 10, SHOTS), 100);
});
