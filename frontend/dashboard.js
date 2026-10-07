/**
 * dashboard.js — DOM wiring for dashboard.html (design/dashboard.md).
 * All decisions (state, highlight index, pill text, counts) come from
 * dashboard_logic.js; this file only fetches, renders and listens.
 *
 * Load order: upload_flow.js, dashboard_logic.js, then this file.
 */
(function () {
  "use strict";

  var L = window.DashboardLogic;
  var STATE = L.STATE;
  var API_BASE = window.RALLY_API_BASE || "";

  var els = {};
  [
    "video-section", "video", "video-error", "state-loading", "loading-message",
    "state-empty", "empty-message", "state-error", "error-message",
    "error-detail-wrap", "error-detail", "state-normal", "timeline", "sum-shots",
    "sum-fh", "sum-bh", "sum-guess-note", "shot-list", "list-toggle", "live",
  ].forEach(function (id) {
    els[id] = document.getElementById(id);
  });

  var uploadId = L.parseUploadId(window.location.search);
  var shots = []; // sorted
  var cardEls = [];
  var dotEls = [];
  var currentIndex = -1;
  var lastManualScrollMs = null;
  var pollTimer = null;

  function showState(name) {
    ["loading", "empty", "error", "normal"].forEach(function (s) {
      els["state-" + s].hidden = s !== name;
    });
  }

  function showError(messages, detail) {
    els["error-message"].textContent = messages.join("\n");
    els["error-detail-wrap"].hidden = !detail;
    els["error-detail"].textContent = detail || "";
    showState("error");
  }

  async function getJson(path) {
    try {
      var resp = await fetch(API_BASE + path, { headers: { Accept: "application/json" } });
      var body;
      try {
        body = await resp.json();
      } catch (e) {
        body = {};
      }
      return { status: resp.status, body: body };
    } catch (networkErr) {
      return { status: 0, body: { error: "network_error" } };
    }
  }

  // ---------------------------------------------------------------- video
  function setupVideo() {
    var v = els.video;
    v.src = API_BASE + "/uploads/" + encodeURIComponent(uploadId) + "/video";
    els["video-section"].hidden = false;
    v.addEventListener("error", function () {
      els["video-section"].hidden = true;
      els["video-error"].hidden = false;
    });
    // §4.3 timeupdate; §4.5 seeking fires repeatedly while the seek bar is dragged.
    ["timeupdate", "seeking", "seeked"].forEach(function (evt) {
      v.addEventListener(evt, syncFromVideo);
    });
    v.addEventListener("loadedmetadata", layoutTimeline);
  }

  function syncFromVideo() {
    if (!shots.length) return;
    setHighlight(L.currentShotIndex(shots, els.video.currentTime, L.SEEK_TOLERANCE_S), false);
  }

  // ------------------------------------------------------------ highlight
  function setHighlight(index, userInitiated) {
    if (index === currentIndex) return;
    currentIndex = index;
    cardEls.forEach(function (card, i) {
      if (i === index) card.setAttribute("aria-current", "true");
      else card.removeAttribute("aria-current");
    });
    dotEls.forEach(function (dot, i) {
      dot.classList.toggle("current", i === index);
      dot.classList.toggle("past", index >= 0 && i < index);
    });
    if (index >= 0) {
      // Move the roving tab stop along so Tab lands on the current shot.
      cardEls.forEach(function (card, i) {
        card.tabIndex = i === index ? 0 : -1;
      });
      if (userInitiated || L.shouldAutoScroll(Date.now(), lastManualScrollMs)) {
        scrollCardIntoView(cardEls[index]);
      }
      // Announce only when not playing, so playback doesn't chatter.
      if (userInitiated || els.video.paused) {
        var c = L.shotCardModel(shots[index], index);
        els.live.textContent = "目前：" + c.title + " " + c.timeText;
      }
    }
  }

  function scrollCardIntoView(card) {
    var list = els["shot-list"];
    if (!card || list.scrollHeight <= list.clientHeight) return;
    var top = card.offsetTop;
    var bottom = top + card.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }

  function seekTo(index) {
    var shot = shots[index];
    if (!shot) return;
    try {
      els.video.currentTime = L.seekTimeFor(shot);
    } catch (e) {
      /* video not loaded yet; highlight anyway */
    }
    setHighlight(index, true); // §4.4: immediately, not on the next timeupdate
  }

  // §4.6: real user scrolling of the list (not our own scrollTop writes).
  function markManualScroll() {
    lastManualScrollMs = Date.now();
  }

  // --------------------------------------------------------------- render
  function pill(model) {
    var span = document.createElement("span");
    span.className = "pill " + model.tone;
    span.textContent = model.text;
    if (model.tooltip) span.title = model.tooltip;
    return span;
  }

  function renderSummary(summary) {
    els["sum-shots"].textContent = String(summary.shotCount);
    [["sum-fh", summary.forehand], ["sum-bh", summary.backhand]].forEach(function (pair) {
      var dd = els[pair[0]];
      dd.textContent = "";
      if (pair[1].tone === L.TONE.VALUE) {
        var n = document.createElement("span");
        n.className = "value";
        n.textContent = pair[1].text;
        dd.appendChild(n);
      } else {
        dd.appendChild(pill(pair[1]));
      }
      if (summary.experimental) {
        var tag = document.createElement("span");
        tag.className = "tag-exp";
        tag.textContent = L.COPY.EXPERIMENTAL;
        tag.title = L.COPY.EXPERIMENTAL_TIP;
        dd.appendChild(tag);
      }
    });
    var note = els["sum-guess-note"];
    note.hidden = summary.guessCount === 0;
    note.textContent = summary.guessCount ? "其中 " + summary.guessCount + " 拍為推測" : "";
  }

  function renderList(cards) {
    var list = els["shot-list"];
    list.textContent = "";
    cardEls = cards.map(function (c, i) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "shot-card";
      btn.tabIndex = i === 0 ? 0 : -1;

      var head = document.createElement("div");
      head.className = "head";
      var title = document.createElement("span");
      title.className = "title";
      title.textContent = c.title;
      var time = document.createElement("span");
      time.className = "time";
      time.textContent = c.timeText;
      head.appendChild(title);
      head.appendChild(time);
      head.appendChild(pill(c.fhBh));

      var line = document.createElement("div");
      line.className = "line";
      line.appendChild(document.createTextNode("球速："));
      line.appendChild(pill(c.ballSpeed));

      btn.appendChild(head);
      btn.appendChild(line);
      btn.addEventListener("click", function () {
        seekTo(i);
      });
      li.appendChild(btn);
      list.appendChild(li);
      return btn;
    });

    list.addEventListener("keydown", function (e) {
      var i = cardEls.indexOf(document.activeElement);
      if (i < 0) return;
      var next = null;
      if (e.key === "ArrowDown" || e.key === "ArrowRight") next = Math.min(i + 1, cardEls.length - 1);
      else if (e.key === "ArrowUp" || e.key === "ArrowLeft") next = Math.max(i - 1, 0);
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = cardEls.length - 1;
      if (next === null) return;
      e.preventDefault();
      markManualScroll();
      cardEls.forEach(function (card, k) {
        card.tabIndex = k === next ? 0 : -1;
      });
      cardEls[next].focus();
    });
    ["wheel", "touchmove", "pointerdown"].forEach(function (evt) {
      list.addEventListener(evt, markManualScroll, { passive: true });
    });
  }

  function renderTimeline() {
    var tl = els.timeline;
    dotEls.forEach(function (d) {
      d.remove();
    });
    dotEls = shots.map(function (shot, i) {
      var dot = document.createElement("span");
      dot.className = "dot";
      dot.title = L.shotCardModel(shot, i).title;
      dot.addEventListener("click", function () {
        seekTo(i);
      });
      tl.appendChild(dot);
      return dot;
    });
    layoutTimeline();
  }

  function layoutTimeline() {
    dotEls.forEach(function (dot, i) {
      dot.style.left = L.timelinePercent(shots[i].contact_time_s, els.video.duration, shots) + "%";
    });
  }

  function setupListToggle() {
    var btn = els["list-toggle"];
    btn.addEventListener("click", function () {
      var collapsed = els["shot-list"].classList.toggle("collapsed");
      btn.setAttribute("aria-expanded", String(!collapsed));
      btn.textContent = collapsed ? "展開逐拍清單" : "收合逐拍清單";
    });
  }

  // ---------------------------------------------------------------- flow
  async function loadShots() {
    var res = await getJson("/uploads/" + encodeURIComponent(uploadId) + "/shots");
    var view = L.buildShotsView(res.status, res.body);
    if (view.state === STATE.ERROR) return showError(view.messages);
    if (view.state === STATE.EMPTY) {
      els["empty-message"].textContent = view.messages[0];
      return showState("empty");
    }
    shots = view.shots;
    renderSummary(view.summary);
    renderList(view.cards);
    renderTimeline();
    currentIndex = -2; // force first paint
    setHighlight(L.currentShotIndex(shots, els.video.currentTime || 0, L.SEEK_TOLERANCE_S), false);
    showState("normal");
  }

  async function checkStatus() {
    var res = await getJson("/uploads/" + encodeURIComponent(uploadId));
    var s = L.classifyUploadStatus(res.status, res.body);
    if (s.state === STATE.LOADING) {
      els["loading-message"].textContent = s.messages[0];
      showState("loading");
      pollTimer = setTimeout(checkStatus, L.POLL_INTERVAL_MS);
      return;
    }
    if (s.state === STATE.READY) return loadShots();
    if (s.state === STATE.FAILED) return showError(s.messages, s.technicalDetail);
    els["video-section"].hidden = true; // 404 etc: there is no video to show either
    showError(s.messages);
  }

  function init() {
    if (!uploadId) {
      // No ?upload=<id>: same situation as a broken link.
      return showError([window.UploadFlow.errorCopyForCode("upload_not_found")]);
    }
    setupListToggle();
    setupVideo(); // §2: the raw video may be played while analysis runs
    checkStatus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.RallyDashboard = {
    seekTo: seekTo,
    stopPolling: function () {
      clearTimeout(pollTimer);
    },
  };
})();
