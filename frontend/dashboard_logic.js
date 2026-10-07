/**
 * dashboard_logic.js — pure, DOM-free logic for the M6 dashboard
 * (design/dashboard.md). No fetch, no DOM: tested under plain Node
 * (tests/test_frontend_dashboard_logic.js) and driven with real backend
 * responses by tests/test_frontend_dashboard_against_backend.py.
 *
 * Error copy is NOT duplicated here: it comes from upload_flow.js
 * (ERROR_COPY_ZH / errorCopyForCode). In the browser upload_flow.js must be
 * loaded first (window.UploadFlow); under Node it is require()d.
 */
(function (root) {
  "use strict";

  var UploadFlow =
    typeof module !== "undefined" && module.exports
      ? require("./upload_flow.js")
      : root.UploadFlow;
  var errorCopyForCode = UploadFlow.errorCopyForCode;

  /** Poll interval while status is queued/processing (§2). */
  var POLL_INTERVAL_MS = 3000;
  /** §4.6: after a manual scroll of the shot list, don't auto-scroll for 3 s. */
  var MANUAL_SCROLL_PAUSE_MS = 3000;
  /** §3.1: fh_bh_confidence below this gets the "推測：" prefix. */
  var GUESS_CONFIDENCE_THRESHOLD = 0.5;
  /**
   * Tolerance the page passes to currentShotIndex(). After a click-to-seek
   * sets video.currentTime = contact_time_s, browsers may report a value a
   * hair below it (float / frame rounding), which under the strict §4.1
   * rule would flip the highlight back to the previous shot. 1 ms is far
   * below one frame (16.7 ms at 60 fps), so it never changes which shot a
   * real playback position belongs to.
   */
  var SEEK_TOLERANCE_S = 0.001;

  var STATE = Object.freeze({
    LOADING: "loading", // queued / processing: skeleton + "分析中"
    READY: "ready", // done: go fetch /shots
    FAILED: "failed", // worker marked the upload failed
    ERROR: "error", // 404 / network / malformed response
    EMPTY: "empty", // done, 0 shots
    NORMAL: "normal", // done, >= 1 shot
  });

  var TONE = Object.freeze({
    PLACEHOLDER: "placeholder", // grey, italic pill: 尚未分析 / 無法判斷 / 不適用
    GUESS: "guess", // secondary style: 推測：X
    VALUE: "value", // a real label
  });

  var COPY = Object.freeze({
    NOT_ANALYZED: "尚未分析",
    NOT_ANALYZED_FHBH_TIP: "正反拍分類功能開發中",
    NOT_ANALYZED_SPEED_TIP: "球速估算功能開發中",
    UNDETERMINED: "無法判斷",
    UNDETERMINED_TIP: "這一拍的畫面不足以判斷正反手",
    NOT_APPLICABLE: "不適用",
    NOT_APPLICABLE_TIP: "發球／高壓球不列入正反手統計",
    GUESS_PREFIX: "推測：",
    GUESS_TIP: "可信度較低，僅供參考",
    EXPERIMENTAL: "實驗中",
    EXPERIMENTAL_TIP: "正反手分類仍在實驗階段，結果可能不準確",
    LOADING: "分析中，完成後會自動顯示擊球資料",
    EMPTY: "這段影片沒有偵測到擊球，可能是影片太短或球員動作不明顯",
    FAILED:
      "這段影片分析時發生錯誤，無法產生擊球資料；請回到上傳頁重新上傳，若持續發生請聯絡我們。",
  });

  var FHBH_LABEL_ZH = Object.freeze({
    forehand: "正手",
    backhand: "反手",
    other: COPY.NOT_APPLICABLE, // ADR 0002: serve / overhead
  });

  /** Reads ?upload=<id> from a location.search string. null if absent/blank. */
  function parseUploadId(search) {
    var params = new URLSearchParams(search || "");
    var id = params.get("upload");
    id = id == null ? "" : id.trim();
    return id === "" ? null : id;
  }

  /** "mm:ss" (or "h:mm:ss" past an hour). Floors, like a video player does. */
  function formatTime(seconds) {
    var s = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var pad = function (n) {
      return (n < 10 ? "0" : "") + n;
    };
    return (h > 0 ? h + ":" + pad(m) : pad(m)) + ":" + pad(sec);
  }

  /** A copy sorted by contact_time_s ascending (§1), ties by shot_index. */
  function sortShots(shots) {
    return (Array.isArray(shots) ? shots.slice() : []).sort(function (a, b) {
      return (
        a.contact_time_s - b.contact_time_s ||
        (a.shot_index || 0) - (b.shot_index || 0)
      );
    });
  }

  /**
   * §4.1/§4.2: index of the LAST shot with contact_time_s <= currentTime, or
   * -1 before the first shot (nothing highlighted). `sortedShots` must be
   * sorted (sortShots). `toleranceS` (default 0 = the exact rule) lets the
   * page absorb seek rounding; see SEEK_TOLERANCE_S.
   */
  function currentShotIndex(sortedShots, currentTime, toleranceS) {
    var t = Number(currentTime);
    if (!Array.isArray(sortedShots) || !isFinite(t)) return -1;
    var limit = t + (toleranceS || 0);
    var lo = 0;
    var hi = sortedShots.length - 1;
    var found = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (sortedShots[mid].contact_time_s <= limit) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  /** §4.4: clicking a shot seeks exactly to its contact time (no pre-roll; see README). */
  function seekTimeFor(shot) {
    return shot.contact_time_s;
  }

  function isGuess(confidence) {
    // A labeled shot with no confidence is treated as a guess: never present
    // an unknown-confidence label as if it were certain.
    return (
      typeof confidence !== "number" ||
      !isFinite(confidence) ||
      confidence < GUESS_CONFIDENCE_THRESHOLD
    );
  }

  /**
   * §3 / §3.1 FH/BH pill for one shot, driven by fh_bh_status only.
   * -> {text, tone, tooltip}
   */
  function fhBhPill(shot) {
    var status = shot && shot.fh_bh_status;
    if (status === "undetermined") {
      return { text: COPY.UNDETERMINED, tone: TONE.PLACEHOLDER, tooltip: COPY.UNDETERMINED_TIP };
    }
    if (status === "labeled") {
      var zh = FHBH_LABEL_ZH[shot.fh_bh_label];
      if (!zh) {
        // labeled but a label value this page doesn't know: don't invent one.
        return { text: COPY.UNDETERMINED, tone: TONE.PLACEHOLDER, tooltip: COPY.UNDETERMINED_TIP };
      }
      var guess = isGuess(shot.fh_bh_confidence);
      if (shot.fh_bh_label === "other") {
        return {
          text: (guess ? COPY.GUESS_PREFIX : "") + zh,
          tone: TONE.PLACEHOLDER,
          tooltip: COPY.NOT_APPLICABLE_TIP,
        };
      }
      if (guess) {
        return { text: COPY.GUESS_PREFIX + zh, tone: TONE.GUESS, tooltip: COPY.GUESS_TIP };
      }
      return { text: zh, tone: TONE.VALUE, tooltip: COPY.EXPERIMENTAL_TIP };
    }
    // "not_analyzed", missing, or any unknown status -> 尚未分析.
    return { text: COPY.NOT_ANALYZED, tone: TONE.PLACEHOLDER, tooltip: COPY.NOT_ANALYZED_FHBH_TIP };
  }

  /**
   * §5: ball speed is ALWAYS 尚未分析 until M5 ships. Deliberately ignores
   * every field of the shot -- ball_speed_kmh is null today, and peak_speed
   * is wrist speed, never ball speed.
   */
  function ballSpeedPill() {
    return { text: COPY.NOT_ANALYZED, tone: TONE.PLACEHOLDER, tooltip: COPY.NOT_ANALYZED_SPEED_TIP };
  }

  /**
   * Summary card (§1, §3.1). Counts only fh_bh_status === "labeled" shots
   * whose label is forehand/backhand; "other" is not counted (ADR 0002).
   * If NO shot is labeled, both counts are a placeholder pill, never 0:
   * "無法判斷" when the classifier ran on every shot and abstained on all,
   * otherwise "尚未分析".
   * -> {shotCount, experimental, forehand:{count, text, tone, tooltip},
   *     backhand:{...}, guessCount}
   */
  function summarize(shots) {
    var list = Array.isArray(shots) ? shots : [];
    var fh = 0;
    var bh = 0;
    var guesses = 0;
    var labeled = 0;
    var undetermined = 0;
    list.forEach(function (s) {
      if (s.fh_bh_status === "labeled" && FHBH_LABEL_ZH[s.fh_bh_label]) {
        labeled += 1;
        if (s.fh_bh_label === "forehand") fh += 1;
        if (s.fh_bh_label === "backhand") bh += 1;
        if (s.fh_bh_label !== "other" && isGuess(s.fh_bh_confidence)) guesses += 1;
      } else if (s.fh_bh_status === "undetermined") {
        undetermined += 1;
      }
    });

    if (labeled === 0) {
      var allAbstained = list.length > 0 && undetermined === list.length;
      var ph = allAbstained
        ? { count: null, text: COPY.UNDETERMINED, tone: TONE.PLACEHOLDER, tooltip: COPY.UNDETERMINED_TIP }
        : { count: null, text: COPY.NOT_ANALYZED, tone: TONE.PLACEHOLDER, tooltip: COPY.NOT_ANALYZED_FHBH_TIP };
      return {
        shotCount: list.length,
        experimental: false,
        forehand: ph,
        backhand: Object.assign({}, ph),
        guessCount: 0,
      };
    }
    var val = function (n) {
      return { count: n, text: String(n), tone: TONE.VALUE, tooltip: COPY.EXPERIMENTAL_TIP };
    };
    return {
      shotCount: list.length,
      experimental: true,
      forehand: val(fh),
      backhand: val(bh),
      guessCount: guesses,
    };
  }

  /** One card's display model (no peak_speed, ever). */
  function shotCardModel(shot, position) {
    return {
      title: "第" + (position + 1) + "拍",
      timeText: formatTime(shot.contact_time_s),
      seekTime: seekTimeFor(shot),
      fhBh: fhBhPill(shot),
      ballSpeed: ballSpeedPill(shot),
    };
  }

  function errorView(httpStatus, body) {
    var code = body && typeof body.error === "string" ? body.error : "unknown";
    return { state: STATE.ERROR, poll: false, errorCode: code, messages: [errorCopyForCode(code)], httpStatus: httpStatus };
  }

  /**
   * GET /uploads/{id} -> what to do next.
   * -> {state: LOADING|READY|FAILED|ERROR, poll, messages, errorCode}
   */
  function classifyUploadStatus(httpStatus, body) {
    if (httpStatus !== 200 || !body || typeof body.status !== "string") {
      return errorView(httpStatus, body);
    }
    switch (body.status) {
      case "queued":
      case "processing":
        return { state: STATE.LOADING, poll: true, messages: [COPY.LOADING], errorCode: null };
      case "done":
        return { state: STATE.READY, poll: false, messages: [], errorCode: null };
      case "failed":
        return {
          state: STATE.FAILED,
          poll: false,
          messages: [COPY.FAILED],
          errorCode: null,
          // English worker exception; shown only under "技術細節", never as main copy.
          technicalDetail: typeof body.error === "string" ? body.error : null,
        };
      default:
        return errorView(httpStatus, { error: "unknown" });
    }
  }

  /**
   * GET /uploads/{id}/shots -> the dashboard view model.
   * -> {state: EMPTY|NORMAL|ERROR, shots (sorted), cards, summary, messages}
   */
  function buildShotsView(httpStatus, body) {
    if (httpStatus !== 200 || !body || !Array.isArray(body.shots)) {
      return errorView(httpStatus, body);
    }
    var shots = sortShots(body.shots);
    if (shots.length === 0) {
      return { state: STATE.EMPTY, shots: [], cards: [], summary: summarize([]), messages: [COPY.EMPTY] };
    }
    return {
      state: STATE.NORMAL,
      shots: shots,
      cards: shots.map(shotCardModel),
      summary: summarize(shots),
      messages: [],
    };
  }

  /** §4.6: may the list auto-scroll now? */
  function shouldAutoScroll(nowMs, lastManualScrollMs) {
    if (lastManualScrollMs == null) return true;
    return nowMs - lastManualScrollMs >= MANUAL_SCROLL_PAUSE_MS;
  }

  /** Timeline dot position (0-100 %). Uses the video duration once known. */
  function timelinePercent(contactTimeS, durationS, sortedShots) {
    var d = Number(durationS);
    if (!isFinite(d) || d <= 0) {
      var last = sortedShots && sortedShots.length ? sortedShots[sortedShots.length - 1].contact_time_s : 0;
      d = last > 0 ? last * 1.05 : 1;
    }
    return Math.min(100, Math.max(0, (contactTimeS / d) * 100));
  }

  var api = {
    STATE: STATE,
    TONE: TONE,
    COPY: COPY,
    POLL_INTERVAL_MS: POLL_INTERVAL_MS,
    MANUAL_SCROLL_PAUSE_MS: MANUAL_SCROLL_PAUSE_MS,
    GUESS_CONFIDENCE_THRESHOLD: GUESS_CONFIDENCE_THRESHOLD,
    SEEK_TOLERANCE_S: SEEK_TOLERANCE_S,
    parseUploadId: parseUploadId,
    formatTime: formatTime,
    sortShots: sortShots,
    currentShotIndex: currentShotIndex,
    seekTimeFor: seekTimeFor,
    fhBhPill: fhBhPill,
    ballSpeedPill: ballSpeedPill,
    summarize: summarize,
    shotCardModel: shotCardModel,
    classifyUploadStatus: classifyUploadStatus,
    buildShotsView: buildShotsView,
    shouldAutoScroll: shouldAutoScroll,
    timelinePercent: timelinePercent,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.DashboardLogic = api;
  }
})(typeof window !== "undefined" ? window : this);
