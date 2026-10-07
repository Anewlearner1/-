// Labeling page (DOM side). The current frame is ALWAYS the decoder frame
// number read from the barcode on the displayed pixels, never derived from
// video.currentTime (browsers can be off by frames; see
// docs/real-footage-findings.md, frame numbering).
(function () {
  "use strict";
  const L = window.LabelLogic;
  const $ = (id) => document.getElementById(id);
  const video = $("video");
  const frameImg = $("frame-img");   // "frames" mode: one server-rendered JPEG per decoder frame
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const hasRVFC = "requestVideoFrameCallback" in HTMLVideoElement.prototype;

  const state = {
    name: null, fps: 0, frameCount: 0,
    curFrame: null,          // last barcode read; null = unreadable
    marks: [], dirty: false,
    corrections: 0,          // seeks whose first landing the barcode had to correct
    mode: "video",           // "video" (VP8 proxy) or "frames" (no VP8 encoder on this PC)
    playing: false,          // frames-mode playback loop
  };
  let queue = Promise.resolve();

  // ---------- barcode reading ----------
  function readFrame() {
    const frames = state.mode === "frames";
    const src = frames ? frameImg : video;
    const w = frames ? frameImg.naturalWidth : video.videoWidth;
    if (!w || (frames ? !frameImg.complete : video.readyState < 2)) return null;
    if (canvas.width !== w) { canvas.width = w; canvas.height = L.BAR_H; }
    ctx.drawImage(src, 0, 0, w, L.BAR_H, 0, 0, w, L.BAR_H);
    const img = ctx.getImageData(0, 0, w, L.BAR_H);
    const r = L.decodeBarcode(img.data, w, L.BAR_H);
    return r.ok ? r.frame : null;
  }

  function refresh() {
    const f = readFrame();
    state.curFrame = f;
    $("frame-num").textContent = f === null ? "?" : String(f);
    $("barcode-warning").hidden = f !== null;
    if (f !== null) {
      const scrub = $("scrub");
      if (document.activeElement !== scrub) scrub.value = String(f);
      $("frame-sub").textContent = "共 " + state.frameCount + " 幀（0 – " + (state.frameCount - 1) +
        "），" + state.fps.toFixed(3) + " fps";
    }
    renderMarks();
    return f;
  }

  // ---------- seeking ----------
  function nextPaint(timeoutMs) {
    return new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      if (hasRVFC) video.requestVideoFrameCallback(() => finish());
      requestAnimationFrame(() => requestAnimationFrame(() => { if (!hasRVFC) finish(); }));
      setTimeout(finish, timeoutMs);
    });
  }

  function setTime(t) {
    const dur = isFinite(video.duration) ? video.duration : Infinity;
    t = Math.max(0, Math.min(t, dur));
    if (Math.abs(video.currentTime - t) < 1e-7) return Promise.resolve();
    return new Promise((resolve) => {
      const onSeeked = () => { video.removeEventListener("seeked", onSeeked); nextPaint(250).then(resolve); };
      video.addEventListener("seeked", onSeeked);
      video.currentTime = t;
    });
  }

  function loadImage(frame) {
    return new Promise((resolve) => {
      frameImg.onload = () => resolve(true);
      frameImg.onerror = () => resolve(false);
      frameImg.src = api(state.name, "frame/" + frame);
    });
  }

  async function doSeek(target) {
    if (!state.fps) return;
    target = L.clampFrame(target, state.frameCount);
    if (state.mode === "frames") {          // exact by construction; still verified by barcode
      await loadImage(target);
      refresh();
      return;
    }
    if (!video.paused) video.pause();
    let t = L.seekTimeForFrame(target, state.fps) + (state.testTimeOffset || 0);
    for (let attempt = 0; attempt < 4; attempt++) {
      await setTime(t);
      const got = refresh();
      if (got === null || got === target) return;
      state.corrections++;
      t += (target - got) / state.fps;     // correct by what the pixels say
    }
  }

  function enqueue(fn) {
    queue = queue.then(fn).catch((e) => console.error(e));
    return queue;
  }
  function seekToFrame(frame) { return enqueue(() => doSeek(frame)); }
  function step(delta) {
    return enqueue(async () => {
      if (state.mode === "frames") state.playing = false;
      else if (!video.paused) { video.pause(); await nextPaint(250); refresh(); }
      if (state.curFrame === null) refresh();
      if (state.curFrame === null) return;          // unreadable: do not guess
      await doSeek(state.curFrame + delta);
    });
  }

  // ---------- playback ----------
  function onPlayFrame() {
    refresh();
    if (!video.paused && !video.ended) video.requestVideoFrameCallback(onPlayFrame);
  }
  video.addEventListener("play", () => { if (hasRVFC) video.requestVideoFrameCallback(onPlayFrame); });
  if (!hasRVFC) video.addEventListener("timeupdate", refresh);
  video.addEventListener("pause", () => { nextPaint(250).then(refresh); });
  async function playFrames() {             // best-effort playback: next frame as fast as it loads
    while (state.playing && state.curFrame !== null && state.curFrame < state.frameCount - 1) {
      const t0 = performance.now();
      await loadImage(state.curFrame + 1);
      refresh();
      const wait = 1000 / state.fps - (performance.now() - t0);
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    }
    state.playing = false;
  }
  function togglePlay() {
    if (state.mode === "frames") {
      if (state.playing) { state.playing = false; return queue; }
      state.playing = true;
      return enqueue(playFrames);
    }
    return enqueue(async () => { if (video.paused) await video.play(); else video.pause(); });
  }

  // ---------- marks ----------
  function mark(stroke) {
    return enqueue(async () => {
      if (state.mode === "frames") state.playing = false;
      else if (!video.paused) { video.pause(); await nextPaint(250); }
      const f = refresh();
      if (f === null) { status("讀不到幀號，沒有標記。", true); return; }
      state.marks = L.addMark(state.marks, f, stroke);
      state.dirty = true;
      status("已標記 #" + f + " " + L.STROKE_ZH[stroke] + "（尚未儲存）");
      renderMarks();
    });
  }
  function removeNearestMark() {
    return enqueue(async () => {
      const f = state.curFrame === null ? 0 : state.curFrame;
      const r = L.removeNearest(state.marks, f);
      if (!r.removed) return;
      state.marks = r.marks;
      state.dirty = true;
      status("已刪除 #" + r.removed.frame + "（尚未儲存）");
      renderMarks();
    });
  }
  function renderMarks() {
    const ul = $("marks");
    $("mark-count").textContent = String(state.marks.length);
    ul.textContent = "";
    state.marks.forEach((m) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.frame = String(m.frame);
      b.dataset.stroke = m.stroke;
      b.textContent = "#" + m.frame + "  " + L.STROKE_ZH[m.stroke];
      if (m.frame === state.curFrame) b.className = "here";
      b.addEventListener("click", () => { b.blur(); seekToFrame(m.frame); });
      li.appendChild(b);
      ul.appendChild(li);
    });
  }

  function status(msg, isWarn) {
    const s = $("save-status");
    s.textContent = msg;
    s.style.color = isWarn ? "#a33" : "";
  }

  // ---------- videos / label I/O ----------
  async function loadList() {
    const r = await fetch("api/videos");
    const data = await r.json();
    $("video-dir").textContent = "影片資料夾：" + data.video_dir;
    const ul = $("video-list");
    ul.textContent = "";
    if (!data.videos.length) ul.innerHTML = "<li class='help'>資料夾裡沒有影片。</li>";
    data.videos.forEach((v) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.name = v.name;
      b.textContent = v.name;
      const badge = document.createElement("span");
      if (!v.has_label) { badge.className = "badge"; badge.textContent = "未標"; }
      else if (v.frame_numbering !== "opencv_decoder") { badge.className = "badge old"; badge.textContent = "舊標記（非解碼器幀號）"; }
      else if (v.complete) { badge.className = "badge done"; badge.textContent = "已標完 " + v.n_contacts; }
      else { badge.className = "badge"; badge.textContent = "未完成 " + v.n_contacts; }
      b.appendChild(badge);
      if (v.name === state.name) b.setAttribute("aria-current", "true");
      b.addEventListener("click", () => { b.blur(); openVideo(v.name); });
      li.appendChild(b);
      ul.appendChild(li);
    });
  }

  function api(name, suffix) { return "api/videos/" + encodeURIComponent(name) + "/" + suffix; }

  async function openVideo(name) {
    if (state.dirty && !confirm("目前的標記還沒儲存，確定要換影片？")) return;
    state.name = name; state.marks = []; state.dirty = false; state.curFrame = null;
    $("no-video").hidden = true; $("player").hidden = false;
    $("label-warning").hidden = true;
    $("complete").checked = false;
    document.querySelectorAll("input[name=hand]").forEach((r) => { r.checked = false; });
    status("");
    renderMarks();
    document.querySelectorAll("#video-list button").forEach((b) =>
      b.setAttribute("aria-current", b.dataset.name === name ? "true" : "false"));
    $("load-status").textContent = "正在準備標記用影片（第一次開啟長影片可能要一兩分鐘）…";
    const infoResp = await fetch(api(name, "info"));
    if (!infoResp.ok) { $("load-status").textContent = "無法開啟這支影片：" + (await infoResp.text()); return; }
    const info = await infoResp.json();
    state.fps = info.fps; state.frameCount = info.frame_count;
    $("scrub").max = String(info.frame_count - 1);
    $("goto").max = String(info.frame_count - 1);

    const lr = await fetch(api(name, "label"));
    if (lr.ok) {
      const { label, meta } = await lr.json();
      if (meta.frame_numbering === "opencv_decoder") {
        state.marks = L.marksFromLabel(label);
        $("complete").checked = !!meta.complete;
        const hand = label.racket_hand || meta.racket_hand;
        if (hand) { const el = document.querySelector("input[name=hand][value=" + hand + "]"); if (el) el.checked = true; }
      } else {
        const w = $("label-warning");
        w.hidden = false;
        w.textContent = "這支影片已有舊標記（" + label.contact_frames.length + " 拍），但幀號不是解碼器幀號（" +
          (meta.frame_numbering || "未記錄") + "），所以沒有載入。請重新標記；儲存時會覆蓋舊檔（舊檔會備份到 data/label_backups/）。";
      }
    }

    state.mode = info.mode === "frames" ? "frames" : "video";
    state.playing = false;
    video.hidden = state.mode === "frames";
    frameImg.hidden = state.mode !== "frames";
    if (state.mode === "video") {
      await new Promise((resolve) => {
        const ok = () => { video.removeEventListener("loadeddata", ok); resolve(); };
        video.addEventListener("loadeddata", ok);
        video.src = api(name, "proxy");
        video.load();
      });
    } else {
      video.removeAttribute("src");
    }
    $("load-status").textContent = state.mode === "frames"
      ? "逐幀圖片模式（這台電腦的 OpenCV 不能產生 VP8 影片）：標註一樣精確，只是播放較慢。" : "";
    await seekToFrame(0);
    renderMarks();
  }

  async function save() {
    if (!state.name) return;
    const handEl = document.querySelector("input[name=hand]:checked");
    if (!handEl) { status("請先選持拍手（右手或左手）。", true); return; }
    const complete = $("complete").checked;
    if (!complete && !confirm("還沒勾「我已從頭看到尾，所有擊球都標了」。要存成「未完成」嗎？")) return;
    let payload;
    try {
      payload = L.buildPayload({ fps: state.fps, frameCount: state.frameCount, marks: state.marks,
                                 complete, racketHand: handEl.value });
    } catch (e) { status("無法儲存：" + e.message, true); return; }
    const r = await fetch(api(state.name, "label"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!r.ok) { status("儲存失敗：" + (await r.text()), true); return; }
    state.dirty = false;
    status("已儲存 " + payload.marks.length + " 拍" + (complete ? "（已完成）" : "（未完成）") + "。");
    loadList();
  }

  // ---------- wiring ----------
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" && (t.type === "number" || t.type === "text") || t.tagName === "TEXTAREA")) return;
    if (e.ctrlKey || e.metaKey || e.altKey || !state.name) return;
    const k = e.key;
    let handled = true;
    if (k === "ArrowRight") step(e.shiftKey ? 10 : 1);
    else if (k === "ArrowLeft") step(e.shiftKey ? -10 : -1);
    else if (k === " " || k === "Spacebar") togglePlay();
    else if (k.toLowerCase() === "f") mark("forehand");
    else if (k.toLowerCase() === "b") mark("backhand");
    else if (k.toLowerCase() === "o") mark("other");
    else if (k === "Delete" || k === "Backspace") removeNearestMark();
    else handled = false;
    if (handled) e.preventDefault();
  });
  $("btn-back10").addEventListener("click", () => step(-10));
  $("btn-back1").addEventListener("click", () => step(-1));
  $("btn-fwd1").addEventListener("click", () => step(1));
  $("btn-fwd10").addEventListener("click", () => step(10));
  $("btn-play").addEventListener("click", () => togglePlay());
  $("btn-goto").addEventListener("click", () => {
    const f = parseInt($("goto").value, 10);
    if (Number.isInteger(f)) seekToFrame(f);
  });
  $("goto").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const f = parseInt($("goto").value, 10); if (Number.isInteger(f)) seekToFrame(f); }
  });
  $("scrub").addEventListener("change", (e) => { seekToFrame(parseInt(e.target.value, 10)); e.target.blur(); });
  $("save").addEventListener("click", (e) => { e.target.blur(); save(); });
  // Buttons keep focus after a click; drop it so Space/arrows reach the page handler.
  document.addEventListener("click", (e) => {
    if (e.target && e.target.tagName === "BUTTON") e.target.blur();
  });
  // Radios/checkbox would otherwise swallow arrows/Space after being clicked.
  document.querySelectorAll("input[type=radio], input[type=checkbox]").forEach((el) =>
    el.addEventListener("change", () => el.blur()));
  window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });

  window.labelPage = { state, seekToFrame, step, mark, save, openVideo, idle: () => queue, readFrame };
  loadList();
})();
