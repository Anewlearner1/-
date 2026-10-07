// Static checks on frontend/dashboard.html + dashboard.js (no browser here):
// every element id the script looks up exists, scripts load in the right
// order, nothing is pulled from a CDN, and the error copy is imported, not
// duplicated.
// Run with: node --test tests/test_frontend_dashboard_page.js

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const FE = path.join(__dirname, "..", "frontend");
const html = fs.readFileSync(path.join(FE, "dashboard.html"), "utf8");
const js = fs.readFileSync(path.join(FE, "dashboard.js"), "utf8");
const logic = fs.readFileSync(path.join(FE, "dashboard_logic.js"), "utf8");

test("every id dashboard.js looks up exists in dashboard.html", () => {
  const block = js.match(/\[\s*("video-section"[\s\S]*?)\]\.forEach/)[1];
  const ids = [...block.matchAll(/"([a-z-]+)"/g)].map((m) => m[1]);
  assert.ok(ids.length > 10);
  for (const s of ["loading", "empty", "error", "normal"]) ids.push("state-" + s);
  for (const id of ids) assert.ok(html.includes(`id="${id}"`), `missing #${id}`);
});

test("scripts: upload_flow.js -> dashboard_logic.js -> dashboard.js, all local", () => {
  const srcs = [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(srcs, ["upload_flow.js", "dashboard_logic.js", "dashboard.js"]);
  assert.ok(!/https?:\/\//.test(html.replace(/<!--[\s\S]*?-->/g, "")), "no external URLs");
});

test("error copy is imported from upload_flow.js, not duplicated", () => {
  assert.ok(!logic.includes("找不到這筆上傳紀錄"));
  assert.ok(!js.includes("找不到這筆上傳紀錄"));
  assert.ok(logic.includes('require("./upload_flow.js")'));
});

test("accessibility hooks: aria-current on cards, live region, list help", () => {
  assert.ok(js.includes('setAttribute("aria-current", "true")'));
  assert.ok(html.includes('aria-live="polite"'));
  assert.ok(html.includes('aria-describedby="list-help"'));
});

test("page never reads peak_speed", () => {
  assert.ok(!js.includes("peak_speed"));
  assert.ok(!/\.peak_speed/.test(logic));
});
