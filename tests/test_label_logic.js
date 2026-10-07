// Node tests for labeling/label_logic.js: barcode decoding from a pixel
// array, mark list operations and the save payload.
// Run with: node --test tests/test_label_logic.js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const L = require(path.join(__dirname, "..", "labeling", "label_logic.js"));

// RGBA strip (same layout as labeling/proxy.py draw_barcode), with optional
// noise, a white level / black level and a horizontal blur at cell edges.
function strip(frame, width, { rows = L.BAR_H, white = 255, black = 0, noise = 0, seed = 1 } = {}) {
  const cells = L.barcodeCells(frame);
  const px = new Uint8ClampedArray(width * rows * 4);
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff - 0.5) * 2;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < width; x++) {
      const c = Math.min(L.N_CELLS - 1, Math.floor((x * L.N_CELLS) / width));
      const v = (cells[c] ? white : black) + noise * rnd();
      const k = (y * width + x) * 4;
      px[k] = px[k + 1] = px[k + 2] = v;
      px[k + 3] = 255;
    }
  }
  return px;
}

test("cells layout: refs at both ends, 20 data bits MSB first, 4 check bits", () => {
  const c = L.barcodeCells(5);
  assert.equal(c.length, 28);
  assert.deepEqual(c.slice(0, 2), [1, 0]);
  assert.deepEqual(c.slice(-2), [1, 0]);
  assert.deepEqual(c.slice(2, 22).join(""), "00000000000000000101");
  assert.equal(parseInt(c.slice(22, 26).join(""), 2), L.checkBits(5));
  assert.equal(L.checkBits(0x12345), 1 ^ 2 ^ 3 ^ 4 ^ 5);
});

test("decodes every frame 0..2000 and large numbers at several widths", () => {
  for (const w of [224, 320, 541, 960]) {
    for (let f = 0; f < 2000; f += w === 320 ? 1 : 37) {
      assert.deepEqual(L.decodeBarcode(strip(f, w), w, L.BAR_H), { ok: true, frame: f });
    }
    for (const f of [65535, 65536, 699050, (1 << 20) - 1]) {
      assert.equal(L.decodeBarcode(strip(f, w), w, L.BAR_H).frame, f);
    }
  }
});

test("tolerates compression-like noise and washed-out levels", () => {
  const px = strip(1234, 640, { white: 200, black: 60, noise: 40 });
  assert.deepEqual(L.decodeBarcode(px, 640, L.BAR_H), { ok: true, frame: 1234 });
});

test("rejects flat frames, missing contrast and corrupted check bits", () => {
  const flat = new Uint8ClampedArray(320 * L.BAR_H * 4).fill(128);
  assert.equal(L.decodeBarcode(flat, 320, L.BAR_H).ok, false);
  const low = strip(77, 320, { white: 140, black: 110 });
  assert.equal(L.decodeBarcode(low, 320, L.BAR_H).reason, "no_contrast");
  const px = strip(77, 280);
  const cellW = 280 / L.N_CELLS;
  for (let y = 0; y < L.BAR_H; y++) {
    for (let x = Math.ceil(cellW * 23); x < Math.floor(cellW * 24); x++) {
      const k = (y * 280 + x) * 4;
      px[k] = px[k + 1] = px[k + 2] = 255 - px[k];
    }
  }
  assert.deepEqual(L.decodeBarcode(px, 280, L.BAR_H), { ok: false, reason: "bad_check" });
});

test("seek time is mid-frame", () => {
  assert.equal(L.seekTimeForFrame(0, 30), 0.5 / 30);
  assert.equal(L.seekTimeForFrame(9, 30), 9.5 / 30);
  assert.equal(L.clampFrame(-3, 10), 0);
  assert.equal(L.clampFrame(12, 10), 9);
});

test("addMark sorts, re-labels the same frame, validates", () => {
  let m = [];
  m = L.addMark(m, 50, "backhand");
  m = L.addMark(m, 10, "forehand");
  m = L.addMark(m, 50, "other");
  assert.deepEqual(m, [{ frame: 10, stroke: "forehand" }, { frame: 50, stroke: "other" }]);
  assert.throws(() => L.addMark(m, 3, "smash"));
  assert.throws(() => L.addMark(m, -1, "forehand"));
  assert.throws(() => L.addMark(m, 1.5, "forehand"));
});

test("removeNearest removes the closest mark, earlier on a tie", () => {
  const m = [{ frame: 10, stroke: "forehand" }, { frame: 20, stroke: "backhand" }, { frame: 40, stroke: "other" }];
  assert.equal(L.removeNearest(m, 33).removed.frame, 40);
  assert.equal(L.removeNearest(m, 15).removed.frame, 10);
  assert.deepEqual(L.removeNearest(m, 0).marks.map((x) => x.frame), [20, 40]);
  assert.equal(L.removeNearest([], 5).removed, null);
  assert.equal(m.length, 3); // not mutated
});

test("buildPayload: sorted marks, complete flag, racket hand; rejects bad state", () => {
  const p = L.buildPayload({
    fps: 29.97, frameCount: 100, complete: 1, racketHand: "left",
    marks: [{ frame: 60, stroke: "backhand" }, { frame: 4, stroke: "forehand" }],
  });
  assert.deepEqual(p, {
    fps: 29.97, complete: true, racket_hand: "left",
    marks: [{ frame: 4, stroke: "forehand" }, { frame: 60, stroke: "backhand" }],
  });
  const ok = { fps: 30, frameCount: 100, complete: false, racketHand: "right", marks: [] };
  assert.equal(L.buildPayload(ok).complete, false);
  assert.throws(() => L.buildPayload({ ...ok, racketHand: null }), /racket hand/);
  assert.throws(() => L.buildPayload({ ...ok, fps: 0 }), /fps/);
  assert.throws(() => L.buildPayload({ ...ok, marks: [{ frame: 100, stroke: "other" }] }), /range/);
  assert.throws(() => L.buildPayload({ ...ok, marks: [{ frame: 5, stroke: "other" }, { frame: 5, stroke: "forehand" }] }), /duplicate/);
});

test("marksFromLabel round-trips a saved label", () => {
  const m = L.marksFromLabel({ contact_frames: [30, 5], stroke_labels: ["backhand", "forehand"] });
  assert.deepEqual(m, [{ frame: 5, stroke: "forehand" }, { frame: 30, stroke: "backhand" }]);
});
