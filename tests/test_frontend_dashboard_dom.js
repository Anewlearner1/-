// Smoke test: runs the real frontend/dashboard.js against a tiny fake DOM
// and a mocked fetch (there is no browser in this repo's environment). Checks
// the wiring, not the visuals: polling -> normal state, aria-current follows
// video time, click-to-seek highlights immediately, 404 -> error copy.
// Run with: node --test tests/test_frontend_dashboard_dom.js

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const FE = path.join(__dirname, "..", "frontend");
const HTML = fs.readFileSync(path.join(FE, "dashboard.html"), "utf8");
const SCRIPTS = ["upload_flow.js", "dashboard_logic.js", "dashboard.js"].map((f) =>
  fs.readFileSync(path.join(FE, f), "utf8")
);

class El {
  constructor(tag, doc) {
    this.tagName = tag;
    this.doc = doc;
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.textContent = "";
    this.className = "";
    this.style = {};
    this.tabIndex = 0;
    this.offsetTop = 0;
    this.offsetHeight = 50;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    const self = this;
    this.classList = {
      toggle(c, force) {
        const set = new Set(self.className.split(" ").filter(Boolean));
        const on = force === undefined ? !set.has(c) : force;
        on ? set.add(c) : set.delete(c);
        self.className = [...set].join(" ");
        return on;
      },
      contains: (c) => self.className.split(" ").includes(c),
    };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t, ev = {}) { (this.listeners[t] || []).forEach((fn) => fn(ev)); }
  focus() { this.doc.activeElement = this; }
}

function setup({ search, responses }) {
  const doc = { readyState: "complete", activeElement: null, byId: {} };
  for (const m of HTML.matchAll(/<(\w+)[^>]*\sid="([^"]+)"/g)) doc.byId[m[2]] = new El(m[1], doc);
  const video = doc.byId.video;
  Object.assign(video, { currentTime: 0, paused: true, duration: 10 });
  doc.getElementById = (id) => doc.byId[id] || null;
  doc.createElement = (tag) => new El(tag, doc);
  doc.createTextNode = (t) => ({ textContent: t });
  doc.addEventListener = () => {};

  const timers = [];
  const calls = [];
  const queue = { ...responses };
  const fetch = async (url) => {
    calls.push(url);
    const key = Object.keys(queue).find((k) => url.endsWith(k));
    const list = queue[key];
    const [status, body] = list.length > 1 ? list.shift() : list[0];
    return { status, json: async () => body };
  };
  const window = { location: { search }, RALLY_API_BASE: "" };
  const ctx = vm.createContext({
    window, document: doc, fetch, URLSearchParams, console, Date, Math, Number, String, JSON, Object,
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
  });
  // `module` is undefined in this context, so the scripts take their browser path.
  SCRIPTS.forEach((src) => vm.runInContext(src, ctx));
  return { doc, video, timers, calls, window };
}

const tick = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 10; i++) await tick(); }

const shot = (i, t, extra = {}) => ({
  shot_index: i, contact_frame: t * 60, contact_time_s: t, peak_speed: 999, wrist: "right",
  fh_bh_label: null, fh_bh_status: "not_analyzed", fh_bh_confidence: null, ball_speed_kmh: null,
  source: "x", ...extra,
});

function current(doc) {
  return doc.byId["shot-list"].children.map((li) => li.children[0].getAttribute("aria-current"));
}

test("queued -> loading + poll; done -> cards; aria-current follows playback and clicks", async () => {
  const shots = [shot(0, 1.0), shot(1, 2.5), shot(2, 4.0, { fh_bh_status: "labeled", fh_bh_label: "backhand", fh_bh_confidence: 0.3 })];
  const { doc, video, timers, calls } = setup({
    search: "?upload=abc",
    responses: {
      "/uploads/abc": [[200, { status: "queued" }], [200, { status: "done" }]],
      "/uploads/abc/shots": [[200, { upload_id: "abc", shot_count: 3, shots }]],
    },
  });
  await settle();
  assert.equal(doc.byId["state-loading"].hidden, false);
  assert.equal(doc.byId["loading-message"].textContent, "分析中，完成後會自動顯示擊球資料");
  assert.equal(video.src, "/uploads/abc/video");
  assert.equal(timers.length, 1, "polls again");

  timers.shift()();
  await settle();
  assert.ok(calls.some((u) => u.endsWith("/shots")));
  assert.equal(doc.byId["state-normal"].hidden, false);
  assert.equal(doc.byId["state-loading"].hidden, true);
  assert.equal(doc.byId["sum-shots"].textContent, "3");
  assert.deepEqual(current(doc), [null, null, null]); // before the first shot

  video.currentTime = 3.0;
  video.dispatch("timeupdate");
  assert.deepEqual(current(doc), [null, "true", null]);

  video.currentTime = 1.2; // dragging the seek bar
  video.dispatch("seeking");
  assert.deepEqual(current(doc), ["true", null, null]);

  const third = doc.byId["shot-list"].children[2].children[0];
  third.dispatch("click");
  assert.equal(video.currentTime, 4.0);
  assert.deepEqual(current(doc), [null, null, "true"]); // immediately, no timeupdate
  assert.match(doc.byId.live.textContent, /第3拍/);

  const text = JSON.stringify(doc.byId["shot-list"], (k, v) => (k === "parent" || k === "doc" ? undefined : v));
  assert.ok(text.includes("推測：反手"));
  assert.ok(!text.includes("999"));
});

test("404 -> error state with upload_not_found copy, no polling", async () => {
  const { doc, timers } = setup({
    search: "?upload=nope",
    responses: { "/uploads/nope": [[404, { error: "upload_not_found", detail: "x" }]] },
  });
  await settle();
  assert.equal(doc.byId["state-error"].hidden, false);
  assert.match(doc.byId["error-message"].textContent, /找不到這筆上傳紀錄/);
  assert.equal(timers.length, 0);
});

test("missing ?upload= -> error state without any fetch", async () => {
  const { doc, calls } = setup({ search: "", responses: {} });
  await settle();
  assert.equal(doc.byId["state-error"].hidden, false);
  assert.equal(calls.length, 0);
});

test("done with 0 shots -> empty state", async () => {
  const { doc } = setup({
    search: "?upload=e",
    responses: {
      "/uploads/e": [[200, { status: "done" }]],
      "/uploads/e/shots": [[200, { upload_id: "e", shot_count: 0, shots: [] }]],
    },
  });
  await settle();
  assert.equal(doc.byId["state-empty"].hidden, false);
  assert.match(doc.byId["empty-message"].textContent, /沒有偵測到擊球/);
});
