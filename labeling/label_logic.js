// Pure logic for the labeling page (no DOM): barcode decoding, mark list
// operations and the save payload. Loaded as a plain <script> in the browser
// (window.LabelLogic) and with require() under `node --test`.
//
// Barcode layout must match labeling/proxy.py: a BAR_H-pixel strip at the top
// of every proxy frame, N_CELLS equal-width cells across the full width:
//   0 white ref, 1 black ref, 2..21 frame number (20 bits, MSB first),
//   22..25 check (XOR of the five nibbles), 26 white ref, 27 black ref.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.LabelLogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const BAR_H = 32;
  const N_DATA_BITS = 20;
  const N_CHECK_BITS = 4;
  const N_CELLS = 2 + N_DATA_BITS + N_CHECK_BITS + 2;
  const MIN_CONTRAST = 60;
  const STROKES = { f: "forehand", b: "backhand", o: "other" };
  const STROKE_ZH = { forehand: "正拍", backhand: "反拍", other: "其他" };

  function checkBits(value) {
    let x = 0;
    for (let s = 0; s < N_DATA_BITS; s += 4) x ^= (value >> s) & 0xf;
    return x;
  }

  // Cells (1 = white) for a frame number; used by tests to synthesise strips.
  function barcodeCells(frame) {
    const cells = [1, 0];
    for (let i = N_DATA_BITS - 1; i >= 0; i--) cells.push((frame >> i) & 1);
    const c = checkBits(frame);
    for (let i = N_CHECK_BITS - 1; i >= 0; i--) cells.push((c >> i) & 1);
    cells.push(1, 0);
    return cells;
  }

  // Decode from RGBA pixels (ImageData.data layout) of the top strip.
  // `height` is the number of rows in `rgba` (at least BAR_H is ideal).
  // Returns {ok: true, frame} or {ok: false, reason}.
  function decodeBarcode(rgba, width, height) {
    const rows = Math.min(height, BAR_H);
    if (!width || rows < 4) return { ok: false, reason: "too_small" };
    const y0 = Math.floor(rows / 4), y1 = rows - Math.floor(rows / 4);
    const means = [];
    for (let i = 0; i < N_CELLS; i++) {
      const a = (i * width) / N_CELLS, b = ((i + 1) * width) / N_CELLS;
      const x0 = Math.floor(a + (b - a) * 0.25);
      const x1 = Math.max(x0 + 1, Math.ceil(b - (b - a) * 0.25));
      let sum = 0, n = 0;
      for (let y = y0; y < y1; y++) {
        for (let x = x0; x < x1; x++) {
          const k = (y * width + x) * 4;
          sum += (rgba[k] + rgba[k + 1] + rgba[k + 2]) / 3;
          n++;
        }
      }
      means.push(sum / n);
    }
    const white = (means[0] + means[N_CELLS - 2]) / 2;
    const black = (means[1] + means[N_CELLS - 1]) / 2;
    if (white - black < MIN_CONTRAST) return { ok: false, reason: "no_contrast" };
    const thr = (white + black) / 2;
    const bits = means.map((m) => (m > thr ? 1 : 0));
    if (bits[0] !== 1 || bits[1] !== 0 || bits[N_CELLS - 2] !== 1 || bits[N_CELLS - 1] !== 0)
      return { ok: false, reason: "bad_reference" };
    let value = 0;
    for (let i = 0; i < N_DATA_BITS; i++) value = value * 2 + bits[2 + i];
    let chk = 0;
    for (let i = 0; i < N_CHECK_BITS; i++) chk = chk * 2 + bits[2 + N_DATA_BITS + i];
    if (chk !== checkBits(value)) return { ok: false, reason: "bad_check" };
    return { ok: true, frame: value };
  }

  // Seek time that lands in the middle of a frame's display interval.
  function seekTimeForFrame(frame, fps) {
    return (frame + 0.5) / fps;
  }

  function clampFrame(frame, frameCount) {
    return Math.max(0, Math.min(frameCount - 1, frame));
  }

  function sortMarks(marks) {
    return marks.slice().sort((a, b) => a.frame - b.frame);
  }

  // Add (or re-label) the mark at `frame`. Returns a new sorted list.
  function addMark(marks, frame, stroke) {
    if (!Number.isInteger(frame) || frame < 0) throw new Error("invalid frame");
    if (!Object.values(STROKES).includes(stroke)) throw new Error("invalid stroke");
    const rest = marks.filter((m) => m.frame !== frame);
    return sortMarks(rest.concat([{ frame, stroke }]));
  }

  // Remove the mark nearest `frame` (ties: the earlier one). Returns
  // {marks, removed} where removed is null if the list was empty.
  function removeNearest(marks, frame) {
    if (!marks.length) return { marks: marks.slice(), removed: null };
    let best = 0;
    for (let i = 1; i < marks.length; i++) {
      const d = Math.abs(marks[i].frame - frame), bd = Math.abs(marks[best].frame - frame);
      if (d < bd || (d === bd && marks[i].frame < marks[best].frame)) best = i;
    }
    const removed = marks[best];
    return { marks: sortMarks(marks.filter((_, i) => i !== best)), removed };
  }

  // Body for POST /api/videos/{name}/label. Throws on invalid state.
  function buildPayload(state) {
    const { fps, frameCount, marks, complete, racketHand } = state;
    if (!(fps > 0)) throw new Error("fps missing");
    if (racketHand !== "right" && racketHand !== "left") throw new Error("racket hand missing");
    const sorted = sortMarks(marks);
    for (let i = 0; i < sorted.length; i++) {
      const f = sorted[i].frame;
      if (!Number.isInteger(f) || f < 0 || (frameCount && f >= frameCount))
        throw new Error("frame out of range: " + f);
      if (i && sorted[i - 1].frame === f) throw new Error("duplicate frame: " + f);
    }
    return {
      fps,
      marks: sorted.map((m) => ({ frame: m.frame, stroke: m.stroke })),
      complete: !!complete,
      racket_hand: racketHand,
    };
  }

  // Marks from a saved label ({contact_frames, stroke_labels}).
  function marksFromLabel(label) {
    const frames = label.contact_frames || [];
    const strokes = label.stroke_labels || [];
    return sortMarks(frames.map((f, i) => ({ frame: f, stroke: STROKES_SET.has(strokes[i]) ? strokes[i] : "other" })));
  }
  const STROKES_SET = new Set(Object.values(STROKES));

  return {
    BAR_H, N_CELLS, N_DATA_BITS, STROKES, STROKE_ZH,
    checkBits, barcodeCells, decodeBarcode, seekTimeForFrame, clampFrame,
    sortMarks, addMark, removeNearest, buildPayload, marksFromLabel,
  };
});
