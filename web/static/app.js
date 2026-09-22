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

  /** One image-viewer widget: toolbar buttons + a scrollable image. */
  function initImageViewer(root) {
    const scroll = root.querySelector(".image-viewer-scroll");
    const img = root.querySelector("img");
    const zoomLabel = root.querySelector(".image-viewer-zoom");
    if (!scroll || !img) return;

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
      scale = clamp((scroll.clientWidth - 4) / img.naturalWidth);
      fitted = true;
      apply();
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

    root.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "zoom-in") zoomBy(ZOOM_STEP);
        else if (action === "zoom-out") zoomBy(1 / ZOOM_STEP);
        else if (action === "fit-width") fitToWidth();
        else if (action === "actual-size") actualSize();
      });
    });

    // Ctrl/Cmd + wheel zooms; a plain wheel scrolls as normal.
    scroll.addEventListener(
      "wheel",
      (event) => {
        if (!(event.ctrlKey || event.metaKey)) return;
        event.preventDefault();
        zoomBy(event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
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
    }
    scroll.addEventListener("dblclick", onDoubleTap);
    scroll.addEventListener("touchend", (event) => {
      const now = Date.now();
      if (now - lastTap < 350) onDoubleTap(event);
      lastTap = now;
    });

    if (img.complete && img.naturalWidth) {
      fitToWidth();
    } else {
      img.addEventListener("load", fitToWidth, { once: true });
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
      if (label) label.textContent = next === "answer" ? "Ответ" : "Вопрос (перечитать)";
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

  document.addEventListener("DOMContentLoaded", () => {
    initImageViewers();
    initAnswerToggle();
    initConfirmForms();
    initHotkeys();
    initPreload();
    initMistakesFilter();
  });
})();
