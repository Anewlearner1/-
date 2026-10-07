/**
 * upload.js — DOM wiring for upload.html. All screen-selection logic lives
 * in upload_flow.js (classifyUploadResponse); this file only:
 *   - talks to POST /upload via fetch (multipart/form-data, field "file",
 *     plus "racket_hand" only when 右手/左手 is picked; body built by
 *     upload_flow.js buildUploadFormData)
 *   - shows/hides the screen <section>s based on what that function
 *     returns
 *   - renders messages_zh / error copy into the DOM
 *
 * Loaded via <script src="upload_flow.js"></script> before this file, so
 * window.UploadFlow is available.
 */
(function () {
  "use strict";

  var SCREEN = window.UploadFlow.SCREEN;
  var classifyUploadResponse = window.UploadFlow.classifyUploadResponse;
  var buildUploadFormData = window.UploadFlow.buildUploadFormData;

  // Backend base URL. Left overridable via a global so this can be pointed
  // at a real running `uvicorn backend.api.upload:app` instance; see
  // frontend/README.md for how to run one for manual testing.
  var API_BASE = window.RALLY_API_BASE || "";

  var sections = {};
  [
    "guidance",
    "loading",
    "info_confirm",
    "submitted",
    "reshoot",
    "error",
  ].forEach(function (name) {
    sections[name] = document.getElementById("screen-" + name);
  });

  function showScreen(name) {
    Object.keys(sections).forEach(function (key) {
      if (!sections[key]) return;
      sections[key].hidden = key !== name;
    });
  }

  function renderMessageList(ulEl, messages, markerClass) {
    ulEl.innerHTML = "";
    messages.forEach(function (msg) {
      var li = document.createElement("li");
      li.className = markerClass || "";
      li.textContent = msg;
      ulEl.appendChild(li);
    });
  }

  function handleResult(result) {
    switch (result.screen) {
      case SCREEN.INFO_CONFIRM:
        renderMessageList(
          document.getElementById("info-messages"),
          result.messages,
          "msg-info"
        );
        document.getElementById("info-continue-btn").onclick = function () {
          showScreen("submitted");
          document.getElementById("submitted-upload-id").textContent =
            result.uploadId || "";
        };
        document.getElementById("info-reupload-btn").onclick = function () {
          resetToGuidance();
        };
        showScreen("info_confirm");
        break;

      case SCREEN.SUBMITTED:
        document.getElementById("submitted-upload-id").textContent =
          result.uploadId || "";
        showScreen("submitted");
        break;

      case SCREEN.RESHOOT:
        renderMessageList(
          document.getElementById("reshoot-messages"),
          result.messages,
          "msg-fail"
        );
        showScreen("reshoot");
        break;

      case SCREEN.ERROR:
        renderMessageList(
          document.getElementById("error-messages"),
          result.messages,
          "msg-error"
        );
        showScreen("error");
        break;

      default:
        resetToGuidance();
    }
  }

  function resetToGuidance() {
    var input = document.getElementById("file-input");
    if (input) input.value = "";
    showScreen("guidance");
  }

  async function uploadFile(file) {
    showScreen("loading");

    // §1.1 racket-hand picker: the checked radio's value is "right", "left"
    // or "" (不確定, the default). buildUploadFormData sends no field for "".
    var checkedHand = document.querySelector(
      'input[name="racket_hand"]:checked'
    );
    var formData = buildUploadFormData(file, checkedHand ? checkedHand.value : "");

    var httpStatus;
    var body;
    try {
      var resp = await fetch(API_BASE + "/upload", {
        method: "POST",
        body: formData,
      });
      httpStatus = resp.status;
      try {
        body = await resp.json();
      } catch (parseErr) {
        body = {};
      }
    } catch (networkErr) {
      // Network-level failure (server unreachable, CORS, etc) -- not one
      // of the backend's own response shapes, so classifyUploadResponse
      // would just see it as "unknown"; build that body directly.
      httpStatus = 0;
      body = { error: "network_error", detail: String(networkErr) };
    }

    var result = classifyUploadResponse(httpStatus, body);
    handleResult(result);
  }

  function init() {
    showScreen("guidance");

    var fileInput = document.getElementById("file-input");
    fileInput.addEventListener("change", function (evt) {
      var file = evt.target.files && evt.target.files[0];
      if (file) uploadFile(file);
    });

    var reshootGuidanceBtn = document.getElementById("reshoot-guidance-btn");
    if (reshootGuidanceBtn) {
      reshootGuidanceBtn.onclick = resetToGuidance;
    }
    var reshootReuploadBtn = document.getElementById("reshoot-reupload-btn");
    if (reshootReuploadBtn) {
      reshootReuploadBtn.onclick = resetToGuidance;
    }
    var errorReuploadBtn = document.getElementById("error-reupload-btn");
    if (errorReuploadBtn) {
      errorReuploadBtn.onclick = resetToGuidance;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Exposed for manual console testing / future automated DOM tests.
  window.RallyUpload = { uploadFile: uploadFile, handleResult: handleResult };
})();
