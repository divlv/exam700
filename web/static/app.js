/*
 * AZ-700 Exam Trainer - client-side behaviour.
 *
 * No framework and no build step: the whole app is server-rendered HTML, and
 * this file only adds what a static page cannot do on its own - the image
 * viewer's zoom/fit controls, keyboard shortcuts on the exam screen, a
 * confirmation prompt on destructive actions, and warming the browser cache
 * for the next image before it is needed.
 */

(function () {
  "use strict";

  const MIN_SCALE = 0.15;
  const MAX_SCALE = 3.0;
  const ZOOM_STEP = 1.25;

  // Every question is its own page load, so the zoom has to be remembered
  // outside the page for it to carry over from one question to the next. It is
  // kept per exam session: the stored value names the session it belongs to, and
  // a different session ignores it and starts from the default (fit to width).
  const ZOOM_STORAGE_KEY = "az700.zoom";

  /** The zoom remembered for this session, or ``null`` if there is none. */
  function readSavedZoom(sessionId) {
    try {
      const saved = JSON.parse(localStorage.getItem(ZOOM_STORAGE_KEY));
      if (saved && saved.session === sessionId && typeof saved.fitted === "boolean") {
        return saved;
      }
    } catch (error) {
      // Storage can be blocked or hold junk; the zoom just starts from default.
    }
    return null;
  }

  function saveZoom(sessionId, fitted, scale) {
    try {
      localStorage.setItem(
        ZOOM_STORAGE_KEY,
        JSON.stringify({ session: sessionId, fitted, scale })
      );
    } catch (error) {
      // Remembering the zoom is a convenience; never let it break the page.
    }
  }

  /** One image-viewer widget: toolbar buttons + a scrollable image. */
  function initImageViewer(root) {
    const scroll = root.querySelector(".image-viewer-scroll");
    const img = root.querySelector("img");
    const zoomLabel = root.querySelector(".image-viewer-zoom");
    if (!scroll || !img) return;

    // Which exam session this viewer belongs to; a viewer without one (none
    // today) simply does not remember its zoom.
    const sessionId = root.dataset.session || null;

    let scale = 1;
    let fitted = true;

    function clamp(value) {
      return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
    }

    function apply() {
      if (!img.naturalWidth) return;
      img.style.width = Math.round(img.naturalWidth * scale) + "px";
      if (zoomLabel) zoomLabel.textContent = Math.round(scale * 100) + "%";
    }

    function fitToWidth() {
      if (!img.naturalWidth) return;
      fitted = true;
      // Never enlarge past 1:1: on a wide screen the image is shown at its
      // native size and centred, instead of being stretched and blurred.
      const fitScale = () => clamp(Math.min(1, (scroll.clientWidth - 4) / img.naturalWidth));
      scale = fitScale();
      apply();
      // A tall image makes a vertical scrollbar appear, which takes width from
      // the viewer on desktop (phones overlay it). If that left the image wider
      // than the viewer, fit once more against the width that is really left.
      if (scroll.scrollWidth > scroll.clientWidth) {
        scale = fitScale();
        apply();
      }
    }

    function actualSize() {
      scale = 1;
      fitted = false;
      apply();
    }

    function zoomBy(factor) {
      scale = clamp(scale * factor);
      fitted = false;
      apply();
    }

    /**
     * Remember the zoom the user just chose, for the next question. Called after
     * the user's own actions only - not after the automatic fit on load or on
     * window resize, which must not overwrite a chosen zoom.
     */
    function remember() {
      if (sessionId) saveZoom(sessionId, fitted, scale);
    }

    /** Apply the zoom remembered for this session, or fit to width by default. */
    function applyInitialZoom() {
      const saved = sessionId ? readSavedZoom(sessionId) : null;
      if (saved && !saved.fitted && Number.isFinite(saved.scale)) {
        scale = clamp(saved.scale);
        fitted = false;
        apply();
      } else {
        fitToWidth();
      }
    }

    root.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "zoom-in") zoomBy(ZOOM_STEP);
        else if (action === "zoom-out") zoomBy(1 / ZOOM_STEP);
        else if (action === "fit-width") fitToWidth();
        else if (action === "actual-size") actualSize();
        else if (action === "copy-image") {
          copyImageToClipboard(img, button);
          return; // not a zoom change
        }
        remember();
      });
    });

    // Ctrl/Cmd + wheel zooms; a plain wheel scrolls as normal.
    scroll.addEventListener(
      "wheel",
      (event) => {
        if (!(event.ctrlKey || event.metaKey)) return;
        event.preventDefault();
        zoomBy(event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
        remember();
      },
      { passive: false }
    );

    // Double-tap / double-click toggles between fit-to-width and 1:1, the
    // same shortcut the desktop viewer's toolbar buttons offer explicitly.
    let lastTap = 0;
    function onDoubleTap(event) {
      event.preventDefault();
      if (fitted) actualSize();
      else fitToWidth();
      remember();
    }
    scroll.addEventListener("dblclick", onDoubleTap);
    scroll.addEventListener("touchend", (event) => {
      const now = Date.now();
      if (now - lastTap < 350) onDoubleTap(event);
      lastTap = now;
    });

    if (img.complete && img.naturalWidth) {
      applyInitialZoom();
    } else {
      img.addEventListener("load", applyInitialZoom, { once: true });
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      if (!fitted) return;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(fitToWidth, 120);
    });

    // Exposed so the question/answer toggle (F2) can re-fit after swapping
    // in an image of a different height without duplicating this logic.
    root._reFit = () => {
      if (fitted) fitToWidth();
    };
  }

  /** Ctrl/Cmd+wheel zoom needs a non-passive listener registered on load. */
  function initImageViewers() {
    document.querySelectorAll(".image-viewer").forEach(initImageViewer);
  }

  /** Triggers a normal browser download of the given URL - the fallback when clipboard image support is missing. */
  function downloadImage(url) {
    const link = document.createElement("a");
    link.href = url;
    link.download = url.split("/").pop() || "image.png";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  /** Shows a temporary status word on the button, then restores its original label. */
  function flashButtonLabel(button, originalLabel, text, holdMs) {
    button.textContent = text;
    setTimeout(() => {
      button.textContent = originalLabel;
      button.disabled = false;
    }, holdMs);
  }

  /**
   * Copies whichever image is currently shown (question or answer, following
   * the F2 toggle) to the system clipboard as a PNG, so it can be pasted
   * straight into another app (e.g. ChatGPT) for a second opinion.
   *
   * The fetched bytes are passed to ClipboardItem as a Promise rather than an
   * already-resolved Blob, and `navigator.clipboard.write` is called without
   * an intervening `await` - both deliberate, so the call chain stays inside
   * the click's user-activation window that Safari enforces strictly. When
   * the Clipboard API for images is unavailable (insecure origin, older
   * Firefox), the image downloads instead so it can still be attached by hand.
   */
  function copyImageToClipboard(img, button) {
    const url = img.src;
    if (!url) return;

    const originalLabel = button.textContent;
    button.disabled = true;

    const canWriteImages = Boolean(
      window.ClipboardItem && navigator.clipboard && navigator.clipboard.write
    );
    if (!canWriteImages) {
      downloadImage(url);
      flashButtonLabel(button, originalLabel, "Файл скачан", 2000);
      return;
    }

    const blobPromise = fetch(url, { credentials: "same-origin" }).then((response) => {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.blob();
    });

    navigator.clipboard
      .write([new ClipboardItem({ "image/png": blobPromise })])
      .then(() => flashButtonLabel(button, originalLabel, "Скопировано!", 1500))
      .catch((error) => {
        console.warn("clipboard copy failed, falling back to download:", error);
        downloadImage(url);
        flashButtonLabel(button, originalLabel, "Не удалось — файл скачан", 2500);
      });
  }

  /**
   * The exam screen's F2 shortcut: swap the shown image between the question
   * and the answer without leaving the grading screen or its state. This
   * replaces the desktop build's separate read-only popup window - a
   * dedicated floating window has no good equivalent on a phone, and an
   * in-place toggle serves the same purpose ("let me read the question
   * again while I decide how to grade myself") on every screen size.
   */
  function initAnswerToggle() {
    const viewer = document.querySelector('.image-viewer[data-role="exam"]');
    const button = document.getElementById("toggle-view-btn");
    if (!viewer || !button) return;

    const img = viewer.querySelector("img");
    const label = document.getElementById("toggle-view-label");

    button.addEventListener("click", () => {
      const showingAnswer = viewer.dataset.showing === "answer";
      const next = showingAnswer ? "question" : "answer";
      viewer.dataset.showing = next;
      img.src = next === "answer" ? viewer.dataset.answerSrc : viewer.dataset.questionSrc;
      // The label names what the next click will show, not what is shown now.
      if (label) label.textContent = next === "question" ? "Ответ" : "Вопрос (перечитать)";
      img.addEventListener("load", () => viewer._reFit && viewer._reFit(), { once: true });
    });
  }

  /** Confirm before submitting a form marked data-confirm="...". */
  function initConfirmForms() {
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        if (!window.confirm(form.dataset.confirm)) event.preventDefault();
      });
    });
  }

  /**
   * Keyboard shortcuts kept from the desktop build, for anyone on a keyboard:
   * Space reveals the answer, 1/2/3 grade, Escape abandons, F2 re-reads.
   * Ignored while typing in a form field so they cannot fight normal input.
   */
  function initHotkeys() {
    document.addEventListener("keydown", (event) => {
      const tag = (event.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      // While a popup is open its keys belong to it: Escape must only close the
      // popup (the browser does that), not also abandon the test behind it.
      if (document.querySelector("dialog[open]")) return;

      const byId = (id) => document.getElementById(id);
      if (event.code === "Space") {
        const reveal = byId("reveal-btn");
        if (reveal) {
          event.preventDefault();
          reveal.click();
        }
      } else if (event.key === "1" || event.key === "2" || event.key === "3") {
        const target = byId(
          { "1": "grade-correct", "2": "grade-partial", "3": "grade-incorrect" }[event.key]
        );
        if (target) target.click();
      } else if (event.key === "Escape") {
        const abandon = byId("abandon-btn");
        if (abandon) abandon.click();
      } else if (event.code === "KeyM" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        // event.code, not event.key, so it works on the Russian layout too.
        const mark = byId("mark-btn");
        if (mark) mark.click();
      } else if (event.key === "F2") {
        const toggle = byId("toggle-view-btn");
        if (toggle) {
          event.preventDefault();
          toggle.click();
        }
      }
    });
  }

  /**
   * Warms the browser's cache for images the user is about to need, so the
   * next question or the answer to this one is already local by the time
   * they tap for it. The server decides which URLs matter (it knows the
   * session plan); this just fetches them.
   */
  function initPreload() {
    const node = document.getElementById("preload-urls");
    if (!node) return;
    try {
      const urls = JSON.parse(node.textContent);
      urls.forEach((url) => {
        const img = new Image();
        img.src = url;
      });
    } catch (error) {
      // Preloading is a nicety; a malformed payload must never break the page.
      console.warn("preload skipped:", error);
    }
  }

  /**
   * The session review screen's "only mistakes" checkbox: hides rows client
   * side, so switching it does not cost a page reload on a phone.
   */
  function initMistakesFilter() {
    const checkbox = document.getElementById("only-mistakes");
    if (!checkbox) return;
    const rows = document.querySelectorAll("[data-mistake]");

    function apply() {
      rows.forEach((row) => {
        const isMistake = row.dataset.mistake === "1";
        row.style.display = checkbox.checked && !isMistake ? "none" : "";
      });
    }

    checkbox.addEventListener("change", apply);
    apply();
  }

  // How often the popup asks the server whether the model has finished, and how
  // long it keeps asking. A request with high reasoning effort takes minutes;
  // after the ceiling the popup stops waiting, but the request itself carries on
  // on the server and the answer is there the next time the question is opened.
  const AI_POLL_MS = 2000;
  const AI_POLL_RETRY_MS = 4000;
  const AI_GIVE_UP_MS = 12 * 60 * 1000;

  /** "42 с" below a minute, "2 мин 05 с" above. */
  function formatElapsed(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds));
    if (seconds < 60) return seconds + " с";
    return Math.floor(seconds / 60) + " мин " + String(seconds % 60).padStart(2, "0") + " с";
  }

  /**
   * The "AI" popup: one dialog shared by every button on the page.
   *
   * A click opens it and asks the server to check the question. The server
   * answers at once if it already has the answer; otherwise it starts the model
   * in the background and the popup polls until the answer is stored. The
   * explanation is shown in a sandboxed frame (it is HTML written by a model).
   * Closing the popup only stops the polling: nothing is lost on the server.
   */
  function initAi() {
    const dialog = document.getElementById("ai-dialog");
    if (!dialog) return;

    const el = (id) => document.getElementById(id);
    const panels = { loading: el("ai-loading"), error: el("ai-error"), result: el("ai-result") };
    const frame = el("ai-frame");
    const questionImg = el("ai-question-img");
    const retryButton = el("ai-retry");

    // One check is followed at a time; `run` ends the callbacks of an older one.
    let run = 0;
    let pollTimer = null;
    let tickTimer = null;
    let state = null;

    function stopTimers() {
      clearTimeout(pollTimer);
      clearInterval(tickTimer);
      pollTimer = null;
      tickTimer = null;
    }

    function show(name) {
      Object.keys(panels).forEach((key) => {
        panels[key].hidden = key !== name;
      });
    }

    function showError(message, canRetry) {
      stopTimers();
      el("ai-error-text").textContent = message;
      retryButton.hidden = !canRetry;
      show("error");
    }

    function showResult(meta, fromStore) {
      stopTimers();
      frame.src = "/ai/" + state.question + "/view";
      const parts = [];
      if (meta && meta.model) parts.push(meta.model);
      if (meta && meta.created_at) parts.push(meta.created_at);
      if (meta && meta.input_tokens !== undefined) {
        parts.push((meta.input_tokens + meta.output_tokens) + " токенов");
      }
      parts.push(fromStore ? "сохранённый ответ" : "сохранено");
      el("ai-meta").textContent = parts.join(" · ");
      show("result");
    }

    /** Count the seconds on screen, continuing from what the server says has passed. */
    function startTicking(elapsedSeconds) {
      const startedAt = Date.now() - elapsedSeconds * 1000;
      const label = el("ai-elapsed");
      const update = () => {
        label.textContent = formatElapsed((Date.now() - startedAt) / 1000);
      };
      clearInterval(tickTimer);
      update();
      tickTimer = setInterval(update, 1000);
    }

    async function call(method, path) {
      const response = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      let data = null;
      try {
        data = await response.json();
      } catch (error) {
        // A login redirect or a proxy error page is not JSON.
      }
      return { ok: response.ok, status: response.status, data };
    }

    /** React to what the server said; `current` guards against a closed or restarted popup. */
    function handle(reply, current) {
      if (current !== run) return;
      const data = reply.data;
      if (!data || !data.state) {
        showError("Сервер вернул неожиданный ответ. Обновите страницу и войдите заново.", true);
        return;
      }
      if (data.state === "done") {
        showResult(data.meta, !state.waited);
      } else if (data.state === "running") {
        if (!state.waited) {
          state.waited = true;
          show("loading");
          startTicking(data.elapsed || 0);
        }
        schedulePoll(current, AI_POLL_MS);
      } else if (data.state === "unconfigured") {
        showError(data.message || "AI не настроен.", false);
      } else if (data.state === "error") {
        showError(data.message || "Не удалось получить ответ модели.", true);
      } else {
        // "idle": the server forgot the request (it was restarted). Ask again.
        begin(current);
      }
    }

    function schedulePoll(current, delay) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(async () => {
        if (current !== run) return;
        if (Date.now() - state.openedAt > AI_GIVE_UP_MS) {
          showError(
            "Ответа всё ещё нет. Запрос продолжает работать на сервере: закройте окно и откройте вопрос позже.",
            false
          );
          return;
        }
        try {
          handle(await call("GET", "/ai/" + state.question + "/status"), current);
        } catch (error) {
          // A phone that went to sleep or a dropped connection: keep waiting.
          schedulePoll(current, AI_POLL_RETRY_MS);
        }
      }, delay);
    }

    async function begin(current) {
      try {
        handle(await call("POST", "/ai/" + state.question), current);
      } catch (error) {
        if (current === run) {
          showError("Нет связи с сервером. Проверьте соединение и повторите.", true);
        }
      }
    }

    function open(question, imageUrl) {
      run += 1;
      stopTimers();
      state = { question, openedAt: Date.now(), waited: false };
      el("ai-dialog-question").textContent = question;
      if (questionImg) {
        questionImg.removeAttribute("src");
        if (imageUrl) questionImg.src = imageUrl;
        const details = questionImg.closest("details");
        if (details) details.open = false;
      }
      frame.src = "about:blank";
      el("ai-elapsed").textContent = "0 с";
      show("loading");
      if (!dialog.open) dialog.showModal();
      begin(run);
    }

    function close() {
      run += 1; // ends the callbacks of the check being followed
      stopTimers();
      frame.src = "about:blank";
    }

    // One listener for every button and table row marked data-ai-open.
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-ai-open]");
      if (!trigger || trigger.disabled) return;
      open(trigger.dataset.question, trigger.dataset.image || "");
    });

    retryButton.addEventListener("click", () => {
      if (!state) return;
      open(state.question, questionImg && questionImg.getAttribute("src"));
    });
    el("ai-dialog-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", close);
    // A click on the dimmed area outside the popup closes it.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initImageViewers();
    initAnswerToggle();
    initConfirmForms();
    initHotkeys();
    initPreload();
    initMistakesFilter();
    initAi();
  });
})();
