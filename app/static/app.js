/**
 * YomiCalibre — Frontend Application
 *
 * Three ways to read the same book, all feeding one mining pipeline:
 *   - Classic PDF  : the original page rendered by pdf.js with an invisible,
 *                    pre-segmented word layer on top (Yomitan-style scanning),
 *                    backed by Tesseract OCR when the page is an image scan.
 *   - Reader       : reflowed, pre-segmented text (EPUB or a PDF text layer).
 *   - Batch i+1    : every sentence diffed against your known Anki vocabulary.
 */

const state = {
  books: [],
  currentBook: null,
  currentChapterIndex: 0,
  chapterData: null,
  sentencesMap: {},
  lastClickedPayload: null,
  activeMode: "reader", // 'pdf' | 'reader' | 'candidates'
  chapterLoadToken: 0,
  extractPollTimer: null,
  settings: {
    anki_deck_name: "Data Mining",
    anki_model_name: "YomiCalibre Mining",
    ankiconnect_url: "http://127.0.0.1:8765",
    auto_mine_on_click: true,
  },
  pdf: {
    lib: null,
    doc: null,
    info: null,
    pageNumber: 0,
    scale: 1.2,
    fitWidth: true,
    layout: null,
    renderToken: 0,
    loadingPage: false,
  },
};

const PDF_MIN_SCALE = 0.4;
const PDF_MAX_SCALE = 4;

document.addEventListener("DOMContentLoaded", async () => {
  initLocalPreferences();
  bindUIEvents();
  await loadSettings();
  await loadLibrary();
  await refreshAnkiBadgeAndTryAutoFlush();
});

// --- Preferences -----------------------------------------------------------

function initLocalPreferences() {
  applyTheme(localStorage.getItem("yc_theme") || "dark");
  applyFontSize(localStorage.getItem("yc_font_size") || "20");
  applyFontFamily(localStorage.getItem("yc_font_family") || "mincho");
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("yc_theme", theme);
  document.querySelectorAll(".seg-btn[data-theme-val]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeVal === theme);
  });
}

function applyFontSize(px) {
  document.documentElement.style.setProperty("--reader-font-size", `${px}px`);
  localStorage.setItem("yc_font_size", px);
  const lbl = document.getElementById("lbl-font-size");
  const rng = document.getElementById("rng-font-size");
  if (lbl) lbl.textContent = `${px}px`;
  if (rng) rng.value = px;
}

function applyFontFamily(type) {
  const stack =
    type === "gothic"
      ? '"Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", sans-serif'
      : '"Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", "MS PMincho", serif';
  document.documentElement.style.setProperty("--reader-font-family", stack);
  localStorage.setItem("yc_font_family", type);
  const sel = document.getElementById("sel-font-family");
  if (sel) sel.value = type;
}

async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    state.settings = data;
    setAutoMineCheckboxes(!!data.auto_mine_on_click);
    document.getElementById("inp-deck-name").value = data.anki_deck_name || "Data Mining";
    document.getElementById("inp-model-name").value =
      data.anki_model_name || "YomiCalibre Mining";
    document.getElementById("inp-ankiconnect-url").value =
      data.ankiconnect_url || "http://127.0.0.1:8765";
    document.getElementById("modal-deck-name").textContent =
      data.anki_deck_name || "Data Mining";

    if (typeof data.known_words_count === "number") {
      document.getElementById("lbl-known-count").textContent =
        data.known_words_count.toLocaleString();
    }
    if (data.dictionary_stats) updateDictStatsLabel(data.dictionary_stats);
  } catch (err) {
    console.error("Failed to load settings:", err);
  }
  loadOcrStatus();
}

function setAutoMineCheckboxes(value) {
  ["chk-auto-mine", "chk-auto-mine-settings"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.checked = value;
  });
}

function updateDictStatsLabel(stats) {
  const lbl = document.getElementById("dict-stats-label");
  if (!lbl) return;
  lbl.textContent = stats.is_full_jmdict
    ? `✅ Full JMdict active (${stats.total_entries.toLocaleString()} entries)`
    : `⚡ Core dictionary active (${stats.total_entries} entries)`;
}

async function loadOcrStatus() {
  const lbl = document.getElementById("ocr-stats-label");
  if (!lbl) return;
  try {
    const res = await fetch("/api/ocr/status");
    const s = await res.json();
    lbl.textContent = s.available
      ? `✅ Tesseract ready (${s.languages.join(", ")}) at ${s.dpi} dpi`
      : `⚠️ ${s.reason}`;
  } catch (_) {
    lbl.textContent = "⚠️ Could not read OCR status.";
  }
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "Size unknown";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

// --- Library ---------------------------------------------------------------

async function loadLibrary(query = "") {
  const grid = document.getElementById("books-grid");
  grid.innerHTML = `<p class="subtitle">Reading metadata.db index (0 Drive bytes accessed)...</p>`;
  try {
    const url = query ? `/api/books?q=${encodeURIComponent(query)}` : "/api/books";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.books = data.books || [];

    const errEl = document.getElementById("library-error");
    if (data.library_error) {
      errEl.textContent = `⚠️ Calibre metadata.db could not be read: ${data.library_error}`;
      errEl.classList.remove("hidden");
    } else {
      errEl.classList.add("hidden");
    }

    if (typeof data.known_words_count === "number") {
      document.getElementById("lbl-known-count").textContent =
        data.known_words_count.toLocaleString();
    }
    renderLibraryGrid();
  } catch (err) {
    grid.innerHTML = `<p class="subtitle">Error loading library index: ${escapeHtml(
      err.message
    )}</p>`;
  }
}

function renderLibraryGrid() {
  const grid = document.getElementById("books-grid");
  if (!state.books.length) {
    grid.innerHTML = `<p class="subtitle">No books found in metadata.db or uploads.</p>`;
    return;
  }

  grid.innerHTML = "";
  for (const book of state.books) {
    const card = document.createElement("div");
    card.className = "book-card";

    const isPdf = (book.preferred_format || "").toUpperCase() === "PDF";
    const badges = [
      `<span class="meta-chip">${escapeHtml(book.preferred_format || "EPUB")}</span>`,
      `<span class="meta-chip">${escapeHtml(formatBytes(book.uncompressed_size))}</span>`,
      book.is_symlink
        ? `<span class="meta-chip" title="Symlink into the rclone Drive mount">☁️ Drive Symlink</span>`
        : `<span class="meta-chip">💾 Local Disk</span>`,
      book.is_extracted
        ? `<span class="meta-chip cached">✅ Cached (${book.total_sentences} sents)</span>`
        : `<span class="meta-chip">⏳ Extract on open</span>`,
    ];
    if (book.source === "Local Upload") {
      badges.push(`<span class="meta-chip">⬆️ Uploaded</span>`);
    }

    card.innerHTML = `
      <div>
        <div class="book-card-title">${escapeHtml(book.title)}</div>
        <div class="book-card-author">${escapeHtml(book.author)}</div>
        <div class="book-meta-badges">${badges.join("\n")}</div>
      </div>
      <div class="book-card-footer">
        <button class="btn-secondary btn-open-reader" style="flex:1;justify-content:center;">
          ${isPdf ? "📄 Open PDF" : "📖 Read"}
        </button>
        <button class="btn-primary btn-open-candidates" style="flex:1;justify-content:center;">
          🎯 Mine i+1
        </button>
      </div>
    `;

    card.querySelector(".btn-open-reader").addEventListener("click", () => {
      openBook(book, isPdf ? "pdf" : "reader");
    });
    card.querySelector(".btn-open-candidates").addEventListener("click", () => {
      openBook(book, "candidates");
    });

    grid.appendChild(card);
  }
}

function isPdfBook(book) {
  return !!book && (book.preferred_format || "").toUpperCase() === "PDF";
}

async function openBook(book, initialMode = "reader", chapterIndex = null) {
  state.currentBook = book;
  const targetChapter =
    chapterIndex !== null
      ? chapterIndex
      : book.progress
      ? book.progress.chapter_index || 0
      : 0;

  document.getElementById("view-library").classList.add("hidden");
  document.getElementById("btn-back-library").classList.remove("hidden");
  document.getElementById("book-mode-tabs").classList.remove("hidden");
  document
    .getElementById("tab-mode-pdf")
    .classList.toggle("hidden", !isPdfBook(book));

  const breadcrumb = document.getElementById("current-book-label");
  breadcrumb.textContent = book.title;
  breadcrumb.classList.remove("hidden");

  closeInspectorDock();
  await switchBookMode(
    initialMode,
    targetChapter,
    (book.progress && book.progress.scroll_ratio) || 0
  );
}

async function switchBookMode(mode, chapterIndex = null, restoreScrollRatio = 0) {
  if (!state.currentBook) return;
  if (mode === "pdf" && !isPdfBook(state.currentBook)) mode = "reader";

  state.activeMode = mode;
  stopExtractionPolling();

  document.getElementById("tab-mode-pdf").classList.toggle("active", mode === "pdf");
  document.getElementById("tab-mode-reader").classList.toggle("active", mode === "reader");
  document.getElementById("tab-mode-candidates").classList.toggle("active", mode === "candidates");

  const show = (id, visible) =>
    document.getElementById(id).classList.toggle("hidden", !visible);
  show("view-pdf", mode === "pdf");
  show("view-reader", mode === "reader");
  show("view-candidates", mode === "candidates");

  closeInspectorDock();

  if (mode === "pdf") {
    await openPdfView();
  } else if (mode === "reader") {
    const targetSec =
      chapterIndex !== null ? chapterIndex : state.currentChapterIndex || 0;
    await loadChapter(state.currentBook.book_id, targetSec, restoreScrollRatio);
  } else {
    await loadBookCandidates(state.currentBook.book_id);
  }
}

function backToLibrary() {
  stopExtractionPolling();
  closeInspectorDock();
  state.currentBook = null;
  state.chapterData = null;
  state.sentencesMap = {};
  releasePdfDocument();

  ["view-reader", "view-candidates", "view-pdf"].forEach((id) =>
    document.getElementById(id).classList.add("hidden")
  );
  document.getElementById("view-library").classList.remove("hidden");
  document.getElementById("btn-back-library").classList.add("hidden");
  document.getElementById("book-mode-tabs").classList.add("hidden");
  document.getElementById("current-book-label").classList.add("hidden");
  return loadLibrary();
}

// --- Reflowed reader -------------------------------------------------------

function stopExtractionPolling() {
  if (state.extractPollTimer) {
    clearTimeout(state.extractPollTimer);
    state.extractPollTimer = null;
  }
}

function showExtractionProgress(job) {
  const box = document.getElementById("reader-progress");
  const bar = document.getElementById("extract-progress-bar");
  const label = document.getElementById("extract-progress-label");
  const detail = document.getElementById("extract-progress-detail");
  if (!box) return;

  box.classList.remove("hidden");
  const percent = job && typeof job.percent === "number" ? job.percent : 0;
  bar.style.width = `${Math.max(3, percent)}%`;

  const phaseLabels = {
    queued: "Queued…",
    waiting: "Waiting for the extraction queue…",
    opening: "Opening the book…",
    segmenting: "Segmenting sentences with SudachiPy…",
    saving: "Saving to the permanent cache…",
    done: "Done",
  };
  label.textContent = `${phaseLabels[(job && job.phase) || "queued"] || "Extracting…"} ${
    percent ? `(${percent}%)` : ""
  }`;
  detail.textContent = (job && job.detail) || "";
}

function hideExtractionProgress() {
  const box = document.getElementById("reader-progress");
  if (box) box.classList.add("hidden");
}

async function loadChapter(bookId, chapterIndex, restoreScrollRatio = 0) {
  const article = document.getElementById("reader-content");
  const token = ++state.chapterLoadToken;
  stopExtractionPolling();
  closeInspectorDock();
  setNavDisabled(true);

  article.innerHTML = `<p class="subtitle">Loading section…</p>`;

  try {
    const res = await fetch(
      `/api/books/${encodeURIComponent(bookId)}/chapter/${chapterIndex}`
    );

    if (res.status === 202) {
      const payload = await res.json();
      article.innerHTML = "";
      showExtractionProgress(payload.job);
      pollExtraction(bookId, chapterIndex, restoreScrollRatio, token);
      return;
    }

    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    if (token !== state.chapterLoadToken) return;

    hideExtractionProgress();
    state.currentChapterIndex = data.chapter_index;
    state.chapterData = data;
    state.sentencesMap = data.sentences || {};

    populateChapterSelect(data.chapters || [], data.chapter_index);

    const hasContent = (data.blocks || []).some(
      (b) => (b.sentences || []).length > 0
    );
    if (!data.total_chapters || !hasContent) {
      renderEmptyBookNotice(data);
    } else {
      renderTokenizedChapter(data);
    }

    const viewport = document.getElementById("reader-viewport");
    if (restoreScrollRatio > 0) {
      requestAnimationFrame(() => {
        const maxScroll = viewport.scrollHeight - viewport.clientHeight;
        if (maxScroll > 0) viewport.scrollTop = Math.round(maxScroll * restoreScrollRatio);
      });
    } else {
      viewport.scrollTop = 0;
    }

    saveCurrentProgress();
  } catch (err) {
    if (token !== state.chapterLoadToken) return;
    hideExtractionProgress();
    setNavDisabled(false);
    article.innerHTML = `<div class="reader-notice">
        <h3>Could not load this section</h3>
        <p>${escapeHtml(err.message || "Unknown error")}</p>
      </div>`;
  }
}

function pollExtraction(bookId, chapterIndex, restoreScrollRatio, token) {
  state.extractPollTimer = setTimeout(async () => {
    if (token !== state.chapterLoadToken) return;
    try {
      const res = await fetch(
        `/api/books/${encodeURIComponent(bookId)}/extract-status`
      );
      const data = await res.json();

      if (data.is_extracted || data.status === "ready") {
        hideExtractionProgress();
        await loadChapter(bookId, chapterIndex, restoreScrollRatio);
        return;
      }
      if (data.status === "error") {
        hideExtractionProgress();
        setNavDisabled(false);
        document.getElementById("reader-content").innerHTML = `<div class="reader-notice">
            <h3>Extraction failed</h3>
            <p>${escapeHtml((data.job && data.job.error) || "Unknown error")}</p>
          </div>`;
        return;
      }
      showExtractionProgress(data.job);
      pollExtraction(bookId, chapterIndex, restoreScrollRatio, token);
    } catch (_) {
      pollExtraction(bookId, chapterIndex, restoreScrollRatio, token);
    }
  }, 800);
}

function renderEmptyBookNotice(data) {
  const article = document.getElementById("reader-content");
  const pdf = isPdfBook(state.currentBook);
  const partiallyIndexed = pdf && (data.total_chapters || 0) > 0;

  article.innerHTML = `
    <div class="reader-notice">
      <h3>${pdf ? "Nothing to reflow here yet" : "Nothing to display"}</h3>
      <p>${
        pdf
          ? partiallyIndexed
            ? "This page of the scan has not been read yet. Open the original PDF view to read it with the OCR word layer, or index every page so the whole book becomes available here too."
            : "This document is an image-only scan, so there is no text to reflow. Open the original PDF view — each page is OCR'd on demand and the detected words stay clickable."
          : "This book produced no readable Japanese text."
      }</p>
      ${pdf ? `<button class="btn-primary" id="btn-goto-pdf">📄 Open the PDF view</button>` : ""}
    </div>`;
  const btn = document.getElementById("btn-goto-pdf");
  if (btn) btn.addEventListener("click", () => switchBookMode("pdf"));
}

function populateChapterSelect(chapters, currentIndex) {
  const sel = document.getElementById("select-chapter");
  sel.innerHTML = "";
  chapters.forEach((ch) => {
    const opt = document.createElement("option");
    opt.value = ch.index;
    opt.textContent = `${ch.index + 1}. ${ch.title}`;
    if (ch.index === currentIndex) opt.selected = true;
    sel.appendChild(opt);
  });

  const total = chapters.length;
  const atStart = currentIndex <= 0;
  const atEnd = currentIndex >= total - 1;

  document.getElementById("btn-prev-chap").disabled = atStart;
  document.getElementById("btn-next-chap").disabled = atEnd;
  document.getElementById("btn-footer-prev").disabled = atStart;
  document.getElementById("btn-footer-next").disabled = atEnd;
  sel.disabled = total <= 1;

  document.getElementById("lbl-section-position").textContent = total
    ? `Section ${currentIndex + 1} / ${total}`
    : "";
}

function setNavDisabled(disabled) {
  ["btn-prev-chap", "btn-next-chap", "btn-footer-prev", "btn-footer-next"].forEach(
    (id) => {
      const el = document.getElementById(id);
      if (el && disabled) el.disabled = true;
    }
  );
}

function goToSection(delta) {
  if (!state.currentBook || state.activeMode !== "reader") return;
  const total = (state.chapterData && state.chapterData.total_chapters) || 0;
  const target = state.currentChapterIndex + delta;
  if (target < 0 || (total && target > total - 1)) return;
  loadChapter(state.currentBook.book_id, target);
}

function renderTokenizedChapter(data) {
  const article = document.getElementById("reader-content");
  article.innerHTML = "";
  const frag = document.createDocumentFragment();

  for (const block of data.blocks || []) {
    const tag = ["h1", "h2", "h3", "h4"].includes(block.type) ? block.type : "p";
    const blockEl = document.createElement(tag);
    blockEl.className = `reader-block ${block.style_class || ""}`.trim();

    for (const sent of block.sentences || []) {
      const sentSpan = document.createElement("span");
      sentSpan.className = "reader-sentence";
      sentSpan.dataset.sid = sent.sid;

      for (const tok of sent.tokens || []) {
        if (!tok.is_word) {
          sentSpan.appendChild(document.createTextNode(tok.surface));
        } else {
          const wordSpan = document.createElement("span");
          wordSpan.className = "jp-word";
          wordSpan.textContent = tok.surface;
          applyWordDataset(wordSpan, tok, sent.sid);
          sentSpan.appendChild(wordSpan);
        }
      }
      blockEl.appendChild(sentSpan);
    }

    frag.appendChild(blockEl);
  }

  article.appendChild(frag);
}

function applyWordDataset(el, tok, sid) {
  el.dataset.wid = tok.wid;
  el.dataset.sid = sid;
  el.dataset.surface = tok.surface;
  el.dataset.base = tok.base || tok.surface;
  el.dataset.normalized = tok.normalized || "";
  el.dataset.hira = tok.reading_hiragana || "";
  el.dataset.kata = tok.reading_katakana || "";
  el.dataset.surfHira = tok.surface_hiragana || "";
  el.dataset.surfKata = tok.surface_katakana || "";
  el.dataset.posDetail = tok.pos_detail || "";
}

// --- Classic PDF view ------------------------------------------------------

async function getPdfLib() {
  if (state.pdf.lib) return state.pdf.lib;
  const lib = await import("/vendor/pdfjs/pdf.mjs");
  lib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.mjs";
  state.pdf.lib = lib;
  return lib;
}

function releasePdfDocument() {
  if (state.pdf.doc) {
    try {
      state.pdf.doc.destroy();
    } catch (_) {}
  }
  state.pdf.doc = null;
  state.pdf.info = null;
  state.pdf.layout = null;
}

function setPdfMessage(html) {
  const el = document.getElementById("pdf-message");
  const stage = document.getElementById("pdf-stage");
  if (html) {
    el.innerHTML = html;
    el.classList.remove("hidden");
    stage.style.visibility = "hidden";
  } else {
    el.classList.add("hidden");
    stage.style.visibility = "visible";
  }
}

async function openPdfView() {
  const book = state.currentBook;
  if (!book) return;

  setPdfMessage("Loading the original PDF…");

  try {
    const infoRes = await fetch(
      `/api/books/${encodeURIComponent(book.book_id)}/pdf/info`
    );
    if (!infoRes.ok) {
      const detail = await infoRes.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${infoRes.status}`);
    }
    state.pdf.info = await infoRes.json();

    const lib = await getPdfLib();
    releaseOnlyDoc();
    const task = lib.getDocument({
      url: `/api/books/${encodeURIComponent(book.book_id)}/file`,
      // Japanese PDFs lean on CJK CMaps and standard fonts
      // Vendored CJK CMaps + standard fonts: many Japanese PDFs use CID-keyed
      // Adobe-Japan1 encodings and render blank without them.
      cMapUrl: "/vendor/pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
      disableAutoFetch: false,
    });
    state.pdf.doc = await task.promise;

    document.getElementById("lbl-pdf-pagecount").textContent = `/ ${state.pdf.doc.numPages}`;
    document.getElementById("inp-pdf-page").max = String(state.pdf.doc.numPages);

    const startPage =
      book.progress && typeof book.progress.chapter_index === "number"
        ? Math.min(Math.max(0, book.progress.chapter_index), state.pdf.doc.numPages - 1)
        : 0;

    await renderPdfPage(startPage);
  } catch (err) {
    console.error("PDF view failed:", err);
    setPdfMessage(
      `<strong>Could not open this PDF.</strong><br/>${escapeHtml(
        err.message || String(err)
      )}`
    );
  }
}

function releaseOnlyDoc() {
  if (state.pdf.doc) {
    try {
      state.pdf.doc.destroy();
    } catch (_) {}
    state.pdf.doc = null;
  }
}

async function renderPdfPage(pageNumber, { forceOcr = false } = {}) {
  const doc = state.pdf.doc;
  const book = state.currentBook;
  if (!doc || !book) return;

  const token = ++state.pdf.renderToken;
  pageNumber = Math.max(0, Math.min(pageNumber, doc.numPages - 1));
  state.pdf.pageNumber = pageNumber;
  state.pdf.loadingPage = true;
  updatePdfToolbar();
  closeInspectorDock();
  setPdfMessage("");

  const canvas = document.getElementById("pdf-canvas");
  const stage = document.getElementById("pdf-stage");
  const layer = document.getElementById("pdf-word-layer");
  layer.innerHTML = "";

  try {
    const page = await doc.getPage(pageNumber + 1);
    if (token !== state.pdf.renderToken) return;

    if (state.pdf.fitWidth) {
      const viewportEl = document.getElementById("pdf-viewport");
      const unscaled = page.getViewport({ scale: 1 });
      const available = Math.max(240, viewportEl.clientWidth - 32);
      state.pdf.scale = clamp(available / unscaled.width, PDF_MIN_SCALE, PDF_MAX_SCALE);
    }

    const outputScale = Math.min(window.devicePixelRatio || 1, 2);
    const viewport = page.getViewport({ scale: state.pdf.scale });

    canvas.width = Math.floor(viewport.width * outputScale);
    canvas.height = Math.floor(viewport.height * outputScale);
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    stage.style.width = `${Math.floor(viewport.width)}px`;
    stage.style.height = `${Math.floor(viewport.height)}px`;

    const ctx = canvas.getContext("2d", { alpha: false });
    await page.render({
      canvasContext: ctx,
      viewport,
      transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
    }).promise;

    if (token !== state.pdf.renderToken) return;
    updatePdfToolbar();

    await loadPdfWordLayer(book.book_id, pageNumber, token, forceOcr);
  } catch (err) {
    console.error("Page render failed:", err);
    if (token === state.pdf.renderToken) {
      setPdfMessage(`<strong>Page render failed.</strong><br/>${escapeHtml(err.message)}`);
    }
  } finally {
    if (token === state.pdf.renderToken) {
      state.pdf.loadingPage = false;
      updatePdfToolbar();
      saveCurrentProgress();
    }
  }
}

async function loadPdfWordLayer(bookId, pageNumber, token, forceOcr) {
  const badge = document.getElementById("pdf-source-badge");
  badge.textContent = forceOcr ? "🔍 Running OCR…" : "⏳ Segmenting…";

  try {
    const url =
      `/api/books/${encodeURIComponent(bookId)}/pdf/page/${pageNumber}` +
      (forceOcr ? "?force_ocr=true" : "");
    const res = await fetch(url);
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    const layout = await res.json();
    if (token !== state.pdf.renderToken) return;

    state.pdf.layout = layout;
    state.sentencesMap = layout.sentences || {};
    renderPdfWordBoxes(layout);

    if (layout.source === "text-layer") {
      badge.textContent = `📝 Text layer · ${layout.words.length} words`;
    } else if (layout.source === "ocr") {
      badge.textContent = `🔍 OCR (${layout.ocr_lang}${
        layout.vertical ? ", vertical" : ""
      }) · ${layout.words.length} words`;
    } else {
      badge.textContent = "⚠️ No text on this page";
      if (!layout.ocr_available && layout.ocr_reason) {
        setPdfMessage(
          `<strong>This page is an image with no text layer, and OCR is unavailable.</strong><br/>${escapeHtml(
            layout.ocr_reason
          )}<br/><br/>The page itself is still displayed below.`
        );
        document.getElementById("pdf-stage").style.visibility = "visible";
      }
    }
  } catch (err) {
    if (token !== state.pdf.renderToken) return;
    badge.textContent = "⚠️ Word layer failed";
    console.error("Word layer failed:", err);
  }
}

function renderPdfWordBoxes(layout) {
  const layer = document.getElementById("pdf-word-layer");
  layer.innerHTML = "";
  const frag = document.createDocumentFragment();

  for (const word of layout.words || []) {
    for (let i = 0; i < word.rects.length; i++) {
      const [x0, y0, x1, y1] = word.rects[i];
      const el = document.createElement("span");
      el.className = "pdf-word";
      el.style.left = `${x0 * 100}%`;
      el.style.top = `${y0 * 100}%`;
      el.style.width = `${Math.max(0.0015, x1 - x0) * 100}%`;
      el.style.height = `${Math.max(0.0015, y1 - y0) * 100}%`;
      el.title = `${word.surface}${
        word.reading_hiragana ? `【${word.reading_hiragana}】` : ""
      }`;
      applyWordDataset(el, word, word.sid);
      frag.appendChild(el);
    }
  }

  layer.appendChild(frag);
}

/**
 * Indexes every page in the background so the batch i+1 tab can diff a whole
 * scanned book against your known vocabulary, not only the pages you opened.
 */
async function analyzeWholePdf() {
  const book = state.currentBook;
  if (!book) return;

  const btn = document.getElementById("btn-pdf-analyze-all");
  const badge = document.getElementById("pdf-source-badge");
  btn.disabled = true;

  try {
    const res = await fetch(
      `/api/books/${encodeURIComponent(book.book_id)}/pdf/analyze-all`,
      { method: "POST" }
    );
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }

    const poll = async () => {
      if (state.activeMode !== "pdf" || state.currentBook !== book) {
        btn.disabled = false;
        return;
      }
      const st = await fetch(
        `/api/books/${encodeURIComponent(book.book_id)}/extract-status`
      ).then((r) => r.json());
      const job = st.job || {};

      if (job.status === "running") {
        badge.textContent = `📚 Indexing ${job.detail || ""} (${job.percent || 0}%)`;
        setTimeout(poll, 900);
        return;
      }

      btn.disabled = false;
      badge.textContent =
        job.status === "error"
          ? `⚠️ Indexing failed: ${job.error || "unknown error"}`
          : "✅ All pages indexed";
      if (job.status !== "error") {
        setTimeout(() => renderPdfPage(state.pdf.pageNumber), 400);
      }
    };
    poll();
  } catch (err) {
    btn.disabled = false;
    badge.textContent = `⚠️ ${err.message}`;
  }
}

function updatePdfToolbar() {
  const doc = state.pdf.doc;
  const n = doc ? doc.numPages : 0;
  const page = state.pdf.pageNumber;

  const input = document.getElementById("inp-pdf-page");
  if (document.activeElement !== input) input.value = String(page + 1);
  document.getElementById("lbl-pdf-pagecount").textContent = `/ ${n}`;
  document.getElementById("lbl-pdf-zoom").textContent = `${Math.round(
    state.pdf.scale * 100
  )}%`;

  document.getElementById("btn-pdf-prev").disabled = state.pdf.loadingPage || page <= 0;
  document.getElementById("btn-pdf-next").disabled =
    state.pdf.loadingPage || page >= n - 1;
  document.getElementById("btn-pdf-ocr").disabled = state.pdf.loadingPage;
}

function goToPdfPage(delta) {
  if (state.activeMode !== "pdf" || state.pdf.loadingPage) return;
  renderPdfPage(state.pdf.pageNumber + delta);
}

function setPdfZoom(scale, { fit = false } = {}) {
  state.pdf.fitWidth = fit;
  if (!fit) state.pdf.scale = clamp(scale, PDF_MIN_SCALE, PDF_MAX_SCALE);
  renderPdfPage(state.pdf.pageNumber);
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

// --- Batch i+1 -------------------------------------------------------------

async function loadBookCandidates(bookId) {
  const listEl = document.getElementById("candidates-list");
  const subEl = document.getElementById("candidates-subtitle");
  listEl.innerHTML = `<p class="subtitle">Computing i+1 sentence candidates against your known vocabulary...</p>`;

  try {
    const res = await fetch(
      `/api/books/${encodeURIComponent(bookId)}/candidates?limit=80`
    );

    if (res.status === 202) {
      const payload = await res.json();
      const job = payload.job || {};
      listEl.innerHTML = `<p class="subtitle">Extracting the book first — ${escapeHtml(
        job.detail || "starting…"
      )} (${job.percent || 0}%)</p>`;
      setTimeout(() => {
        if (state.activeMode === "candidates") loadBookCandidates(bookId);
      }, 1000);
      return;
    }

    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    const cands = data.candidates || [];

    subEl.textContent = `Book sentences: ${data.total_book_sentences} · Known words: ${data.known_words_count} · Top ${cands.length} candidates ranked by frequency`;

    if (!cands.length) {
      // A PDF is read page by page, so an empty result usually just means the rest
      // of the book has not been indexed (or OCR'd) yet.
      const needsIndexing = isPdfBook(state.currentBook);
      listEl.innerHTML = `<div class="reader-notice">
          <h3>No candidate sentences yet</h3>
          <p>${
            needsIndexing
              ? "This PDF has no extracted text. Index every page first — pages without a text layer are run through OCR."
              : "Every sentence in this book is already covered by your known vocabulary, or the book has no Japanese text."
          }</p>
          ${needsIndexing ? `<button class="btn-primary" id="btn-index-from-candidates">📚 Index all pages</button>` : ""}
        </div>`;
      const indexBtn = document.getElementById("btn-index-from-candidates");
      if (indexBtn) {
        indexBtn.addEventListener("click", async () => {
          indexBtn.disabled = true;
          indexBtn.textContent = "⏳ Indexing…";
          await fetch(
            `/api/books/${encodeURIComponent(bookId)}/pdf/analyze-all`,
            { method: "POST" }
          );
          const poll = async () => {
            const st = await fetch(
              `/api/books/${encodeURIComponent(bookId)}/extract-status`
            ).then((r) => r.json());
            const job = st.job || {};
            if (job.status === "running") {
              indexBtn.textContent = `⏳ ${job.detail || ""} (${job.percent || 0}%)`;
              setTimeout(poll, 900);
            } else {
              loadBookCandidates(bookId);
            }
          };
          poll();
        });
      }
      return;
    }

    listEl.innerHTML = "";
    for (const cand of cands) {
      listEl.appendChild(buildCandidateItem(cand, data));
    }
  } catch (err) {
    listEl.innerHTML = `<p class="subtitle">Error computing i+1 candidates: ${escapeHtml(
      err.message
    )}</p>`;
  }
}

function buildCandidateItem(cand, data) {
  const item = document.createElement("div");
  item.className = "mined-card-item";
  const frDefs = (cand.definitions_fr || []).join(" ; ");
  const enDefs = (cand.definitions_en || []).join(" ; ");

  item.innerHTML = `
    <div class="mined-card-top">
      <div>
        <span class="mined-card-word">${escapeHtml(cand.base)}</span>
        <span class="mined-card-reading">【${escapeHtml(cand.reading_hiragana)} / ${escapeHtml(
    cand.reading_katakana
  )}】</span>
        <span class="insp-pos-badge">${escapeHtml(cand.pos_detail)}</span>
        <span class="insp-freq-badge">Freq #${cand.frequency_rank} · ${
    cand.book_occurrences
  }× in book</span>
      </div>
      <div><button class="btn-primary btn-mine-cand">➕ Mine to Anki</button></div>
    </div>
    <div class="mined-card-sentence">${cand.sentence_html}</div>
    <div class="mined-card-defs">
      ${frDefs ? `<div>🇫🇷 ${escapeHtml(frDefs)}</div>` : ""}
      ${enDefs ? `<div>🇬🇧 ${escapeHtml(enDefs)}</div>` : ""}
    </div>
  `;

  const mineBtn = item.querySelector(".btn-mine-cand");
  mineBtn.addEventListener("click", async () => {
    mineBtn.disabled = true;
    mineBtn.textContent = "⏳...";
    try {
      const r = await fetch("/api/word/click", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: data.book_id,
          book_title: data.book_title,
          chapter_title: cand.section_title,
          wid: cand.wid,
          sid: cand.sid,
          surface: cand.surface,
          base: cand.base,
          reading_hiragana: cand.reading_hiragana,
          reading_katakana: cand.reading_katakana,
          pos_detail: cand.pos_detail,
          sentence_tokens: cand.sentence_tokens,
          force_mine: true,
        }),
      });
      const out = await r.json();
      if (out.mined_card && !out.mined_card.anki_synced) {
        await tryPushCardViaBrowserAnkiConnect(
          out.mined_card,
          state.settings.anki_deck_name || "Data Mining",
          state.settings.anki_model_name || "YomiCalibre Mining",
          out.mined_card.anki_model_spec
        );
      }
      mineBtn.textContent = "✅ Mined";
      await refreshAnkiBadgeAndTryAutoFlush();
    } catch (err) {
      mineBtn.textContent = "⚠️ Failed";
      mineBtn.disabled = false;
    }
  });

  return item;
}

// --- Word click & inspector dock -------------------------------------------

async function handleWordClick(wordEl) {
  document
    .querySelectorAll(".jp-word.active-word, .pdf-word.active-word")
    .forEach((el) => el.classList.remove("active-word"));
  document
    .querySelectorAll(".reader-sentence.active-sentence")
    .forEach((el) => el.classList.remove("active-sentence"));

  // A word split across lines is several boxes: highlight all of them.
  const wid = wordEl.dataset.wid;
  if (wordEl.classList.contains("pdf-word")) {
    document
      .querySelectorAll(`.pdf-word[data-wid="${cssEscape(wid)}"]`)
      .forEach((el) => el.classList.add("active-word"));
  } else {
    wordEl.classList.add("active-word");
    const parentSentEl = wordEl.closest(".reader-sentence");
    if (parentSentEl) parentSentEl.classList.add("active-sentence");
  }

  const sid = wordEl.dataset.sid;
  const sentenceObj = state.sentencesMap[sid];
  const sentenceTokens = sentenceObj ? sentenceObj.tokens : [];

  const payload = {
    book_id: state.currentBook ? state.currentBook.book_id : "",
    book_title: state.currentBook ? state.currentBook.title : "",
    chapter_title: currentSectionTitle(),
    wid: wid,
    sid: sid,
    surface: wordEl.dataset.surface,
    base: wordEl.dataset.base,
    normalized: wordEl.dataset.normalized,
    reading_hiragana: wordEl.dataset.hira,
    reading_katakana: wordEl.dataset.kata,
    surface_hiragana: wordEl.dataset.surfHira,
    surface_katakana: wordEl.dataset.surfKata,
    pos_detail: wordEl.dataset.posDetail,
    sentence_tokens: sentenceTokens,
  };
  state.lastClickedPayload = payload;

  openInspectorDockLoading(payload);
  requestAnimationFrame(() => ensureWordVisibleInViewport(wordEl));

  try {
    const res = await fetch("/api/word/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await res.json();
    renderInspectorDockResult(result);

    if (result.mined_card) {
      if (!result.mined_card.anki_synced) {
        const pushedLocally = await tryPushCardViaBrowserAnkiConnect(
          result.mined_card,
          state.settings.anki_deck_name || "Data Mining",
          state.settings.anki_model_name || "YomiCalibre Mining",
          result.mined_card.anki_model_spec
        );
        if (pushedLocally) {
          document.getElementById(
            "insp-anki-status"
          ).textContent = `✅ Added to Anki (${state.settings.anki_deck_name})`;
        }
      }
      await refreshAnkiBadgeAndTryAutoFlush();
    }
  } catch (err) {
    console.error("Word lookup failed:", err);
    document.getElementById("insp-definitions").innerHTML =
      `<span class="def-line-en">Lookup failed: ${escapeHtml(err.message)}</span>`;
  }
}

function currentSectionTitle() {
  if (state.activeMode === "pdf") return `Page ${state.pdf.pageNumber + 1}`;
  return state.chapterData ? state.chapterData.chapter_title : "";
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}

function openInspectorDockLoading(payload) {
  document.getElementById("bottom-inspector-dock").classList.remove("hidden");
  document.getElementById("insp-word-base").textContent = payload.base || payload.surface;

  const surfEl = document.getElementById("insp-word-surface");
  if (payload.surface && payload.surface !== payload.base) {
    surfEl.textContent = `surface: ${payload.surface}`;
    surfEl.classList.remove("hidden");
  } else {
    surfEl.classList.add("hidden");
  }

  document.getElementById("insp-pos").textContent = payload.pos_detail || "Word";
  document.getElementById("insp-freq").textContent = "";
  document.getElementById("insp-hira").textContent = payload.reading_hiragana || "...";
  document.getElementById("insp-kata").textContent = payload.reading_katakana || "...";
  document.getElementById("insp-definitions").innerHTML =
    `<span class="def-line-en">Looking up definition...</span>`;
  document.getElementById("insp-anki-status").textContent = "⏳...";
}

function renderInspectorDockResult(result) {
  document.getElementById("insp-word-base").textContent = result.base || result.surface;
  document.getElementById("insp-hira").textContent = result.reading_hiragana || "";
  document.getElementById("insp-kata").textContent = result.reading_katakana || "";
  document.getElementById("insp-pos").textContent = result.pos_detail || "Word";
  document.getElementById("insp-freq").textContent = result.frequency_rank
    ? `Freq #${result.frequency_rank}`
    : "";

  const frList = result.definitions_fr || [];
  const enList = result.definitions_en || [];
  let defsHtml = "";
  if (frList.length) defsHtml += `<div class="def-line-fr">🇫🇷 ${escapeHtml(frList.join(" ; "))}</div>`;
  if (enList.length) defsHtml += `<div class="def-line-en">🇬🇧 ${escapeHtml(enList.join(" ; "))}</div>`;
  if (!frList.length && !enList.length) {
    defsHtml = `<div class="def-line-en">Inflected / Proper term (${escapeHtml(
      result.surface_hiragana || result.reading_hiragana
    )})</div>`;
  }
  document.getElementById("insp-definitions").innerHTML = defsHtml;
  document.getElementById("insp-sentence-html").innerHTML = result.sentence_html || "";

  const statusEl = document.getElementById("insp-anki-status");
  const manualBtn = document.getElementById("btn-manual-mine");
  if (result.mined_card) {
    manualBtn.classList.add("hidden");
    statusEl.classList.remove("hidden");
    statusEl.textContent = result.mined_card.anki_synced
      ? `✅ Added to Anki (${state.settings.anki_deck_name})`
      : `📥 Queued in '${state.settings.anki_deck_name}'`;
  } else {
    statusEl.classList.add("hidden");
    manualBtn.classList.remove("hidden");
  }
}

function closeInspectorDock() {
  const dock = document.getElementById("bottom-inspector-dock");
  if (dock) dock.classList.add("hidden");
  document
    .querySelectorAll(".jp-word.active-word, .pdf-word.active-word")
    .forEach((el) => el.classList.remove("active-word"));
  document
    .querySelectorAll(".reader-sentence.active-sentence")
    .forEach((el) => el.classList.remove("active-sentence"));
}

function ensureWordVisibleInViewport(wordEl) {
  const viewport =
    state.activeMode === "pdf"
      ? document.getElementById("pdf-viewport")
      : document.getElementById("reader-viewport");
  if (!viewport) return;

  const vpRect = viewport.getBoundingClientRect();
  const wordRect = wordEl.getBoundingClientRect();
  if (wordRect.bottom > vpRect.bottom - 24) {
    viewport.scrollBy({
      top: wordRect.bottom - (vpRect.bottom - 48),
      behavior: "smooth",
    });
  }
}

// --- Browser-to-AnkiConnect bridge -----------------------------------------

async function tryPushCardViaBrowserAnkiConnect(card, deckName, modelName, modelSpec) {
  const urlsToTry = ["http://127.0.0.1:8765", "http://localhost:8765"];
  if (
    state.settings.ankiconnect_url &&
    !urlsToTry.includes(state.settings.ankiconnect_url)
  ) {
    urlsToTry.unshift(state.settings.ankiconnect_url);
  }

  for (const url of urlsToTry) {
    try {
      await invokeAnkiConnectBrowser(url, "createDeck", { deck: deckName });

      const models = await invokeAnkiConnectBrowser(url, "modelNames", {});
      if (Array.isArray(models) && !models.includes(modelName) && modelSpec) {
        await invokeAnkiConnectBrowser(url, "createModel", modelSpec);
      }

      const noteId = await invokeAnkiConnectBrowser(url, "addNote", {
        note: {
          deckName: deckName,
          modelName: modelName,
          fields: card.anki_fields,
          options: { allowDuplicate: false },
          tags: ["yomicalibre", "japanese-mining"],
        },
      });

      if (noteId) {
        await fetch(`/api/anki/cards/${card.id}/mark-synced`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ anki_note_id: noteId }),
        });
        return true;
      }
    } catch (_) {}
  }
  return false;
}

async function invokeAnkiConnectBrowser(url, action, params) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 1600);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, version: 6, params }),
      signal: ctrl.signal,
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    return data.result;
  } finally {
    clearTimeout(timer);
  }
}

async function refreshAnkiBadgeAndTryAutoFlush() {
  try {
    const res = await fetch("/api/anki/cards?only_unsynced=true");
    const data = await res.json();
    const unsyncedCards = data.cards || [];
    updateUnsyncedBadge(unsyncedCards.length);

    if (typeof data.known_words_count === "number") {
      document.getElementById("lbl-known-count").textContent =
        data.known_words_count.toLocaleString();
    }

    if (unsyncedCards.length > 0) {
      let flushedCount = 0;
      for (const card of unsyncedCards) {
        const ok = await tryPushCardViaBrowserAnkiConnect(
          card,
          data.deck_name || "Data Mining",
          data.model_name || "YomiCalibre Mining",
          data.model_spec
        );
        if (ok) flushedCount++;
        else break;
      }
      if (flushedCount > 0) updateUnsyncedBadge(unsyncedCards.length - flushedCount);
    }
  } catch (_) {}
}

function updateUnsyncedBadge(count) {
  const badge = document.getElementById("unsynced-badge");
  if (!badge) return;
  if (count > 0) {
    badge.textContent = String(count);
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}

function saveCurrentProgress() {
  if (!state.currentBook) return;

  let chapterIndex = state.currentChapterIndex;
  let ratio = 0;

  if (state.activeMode === "pdf") {
    chapterIndex = state.pdf.pageNumber;
  } else if (state.activeMode === "reader") {
    const viewport = document.getElementById("reader-viewport");
    const maxScroll = viewport.scrollHeight - viewport.clientHeight;
    ratio = maxScroll > 0 ? viewport.scrollTop / maxScroll : 0;
  } else {
    return;
  }

  fetch(`/api/books/${encodeURIComponent(state.currentBook.book_id)}/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      book_title: state.currentBook.title,
      chapter_index: chapterIndex,
      scroll_ratio: Number(ratio.toFixed(4)),
    }),
  }).catch(() => {});
}

async function triggerAnkiSnapshotSync() {
  try {
    const res = await fetch("/api/anki/sync-snapshot", { method: "POST" });
    const data = await res.json();
    if (typeof data.total_known_words === "number") {
      document.getElementById("lbl-known-count").textContent =
        data.total_known_words.toLocaleString();
    }
    return data;
  } catch (_) {
    return null;
  }
}

// --- Uploads ---------------------------------------------------------------

function setUploadStatus(message, kind = "") {
  const el = document.getElementById("upload-status");
  if (!el) return;
  if (!message) {
    el.classList.add("hidden");
    return;
  }
  el.className = `upload-status ${kind ? `is-${kind}` : ""}`.trim();
  el.innerHTML = message;
  el.classList.remove("hidden");
}

function uploadBookWithProgress(file) {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/books/upload");

    xhr.upload.addEventListener("progress", (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      setUploadStatus(
        `⬆️ Uploading <strong>${escapeHtml(file.name)}</strong> — ${pct}%`
      );
    });

    xhr.addEventListener("load", () => {
      let body = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body.detail || `Upload failed (HTTP ${xhr.status})`));
    });
    xhr.addEventListener("error", () => reject(new Error("Network error during upload")));
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));

    xhr.send(formData);
  });
}

async function handleUploadSelection(file) {
  if (!file) return;
  const name = file.name.toLowerCase();
  if (!name.endsWith(".epub") && !name.endsWith(".pdf")) {
    setUploadStatus("⚠️ Only .epub and .pdf files are supported.", "error");
    return;
  }

  try {
    setUploadStatus(`⬆️ Uploading <strong>${escapeHtml(file.name)}</strong>…`);
    const result = await uploadBookWithProgress(file);
    state.books = result.books || state.books;
    renderLibraryGrid();

    const book =
      result.book || state.books.find((b) => b.book_id === result.book_id) || null;

    if (!book) {
      setUploadStatus(
        `⚠️ <strong>${escapeHtml(
          result.filename || file.name
        )}</strong> was saved but is not listed. Try refreshing the library.`,
        "error"
      );
      return;
    }

    setUploadStatus(
      `✅ <strong>${escapeHtml(book.title)}</strong> imported (${formatBytes(
        result.size_bytes
      )}). Opening…`,
      "ok"
    );
    setTimeout(() => setUploadStatus(""), 4000);
    await openBook(book, isPdfBook(book) ? "pdf" : "reader");
  } catch (err) {
    setUploadStatus(`⚠️ ${escapeHtml(err.message)}`, "error");
  }
}

// --- Events ----------------------------------------------------------------

function bindUIEvents() {
  bindReaderEvents();
  bindPdfEvents();
  bindLibraryEvents();
  bindModalEvents();
  bindGlobalKeyboard();
}

function bindReaderEvents() {
  const readerViewport = document.getElementById("reader-viewport");

  readerViewport.addEventListener("click", (e) => {
    const wordEl = e.target.closest(".jp-word");
    if (wordEl) {
      e.stopPropagation();
      handleWordClick(wordEl);
    } else if (!e.target.closest(".chapter-footer-nav, .reader-notice")) {
      closeInspectorDock();
    }
  });

  let scrollTimer = null;
  readerViewport.addEventListener("scroll", () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(saveCurrentProgress, 600);
  });

  attachSwipeNavigation(readerViewport, (dir) => goToSection(dir));

  document.getElementById("btn-close-inspector").addEventListener("click", closeInspectorDock);

  document.getElementById("btn-manual-mine").addEventListener("click", async () => {
    if (!state.lastClickedPayload) return;
    const res = await fetch("/api/word/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...state.lastClickedPayload, force_mine: true }),
    });
    const result = await res.json();
    renderInspectorDockResult(result);
    if (result.mined_card && !result.mined_card.anki_synced) {
      await tryPushCardViaBrowserAnkiConnect(
        result.mined_card,
        state.settings.anki_deck_name || "Data Mining",
        state.settings.anki_model_name || "YomiCalibre Mining",
        result.mined_card.anki_model_spec
      );
    }
    await refreshAnkiBadgeAndTryAutoFlush();
  });

  document.getElementById("tab-mode-pdf").addEventListener("click", () => switchBookMode("pdf"));
  document.getElementById("tab-mode-reader").addEventListener("click", () => switchBookMode("reader"));
  document.getElementById("tab-mode-candidates").addEventListener("click", () => switchBookMode("candidates"));

  document.getElementById("btn-back-library").addEventListener("click", backToLibrary);

  document.getElementById("select-chapter").addEventListener("change", (e) => {
    if (!state.currentBook) return;
    loadChapter(state.currentBook.book_id, parseInt(e.target.value, 10));
  });

  document.getElementById("btn-prev-chap").addEventListener("click", () => goToSection(-1));
  document.getElementById("btn-next-chap").addEventListener("click", () => goToSection(1));
  document.getElementById("btn-footer-prev").addEventListener("click", () => goToSection(-1));
  document.getElementById("btn-footer-next").addEventListener("click", () => goToSection(1));

  ["chk-auto-mine", "chk-auto-mine-settings"].forEach((id) => {
    document.getElementById(id).addEventListener("change", async (e) => {
      const val = e.target.checked;
      state.settings.auto_mine_on_click = val;
      setAutoMineCheckboxes(val);
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_mine_on_click: val }),
      });
    });
  });
}

function bindPdfEvents() {
  const layer = document.getElementById("pdf-word-layer");
  layer.addEventListener("click", (e) => {
    const wordEl = e.target.closest(".pdf-word");
    if (wordEl) {
      e.stopPropagation();
      handleWordClick(wordEl);
    }
  });

  const viewport = document.getElementById("pdf-viewport");
  viewport.addEventListener("click", (e) => {
    if (!e.target.closest(".pdf-word")) closeInspectorDock();
  });
  attachSwipeNavigation(viewport, (dir) => goToPdfPage(dir));

  document.getElementById("btn-pdf-prev").addEventListener("click", () => goToPdfPage(-1));
  document.getElementById("btn-pdf-next").addEventListener("click", () => goToPdfPage(1));

  document.getElementById("inp-pdf-page").addEventListener("change", (e) => {
    const n = parseInt(e.target.value, 10);
    if (!Number.isNaN(n)) renderPdfPage(n - 1);
  });

  document.getElementById("btn-pdf-zoom-in").addEventListener("click", () =>
    setPdfZoom(state.pdf.scale * 1.25)
  );
  document.getElementById("btn-pdf-zoom-out").addEventListener("click", () =>
    setPdfZoom(state.pdf.scale / 1.25)
  );
  document.getElementById("btn-pdf-fit").addEventListener("click", () =>
    setPdfZoom(state.pdf.scale, { fit: true })
  );
  document.getElementById("btn-pdf-ocr").addEventListener("click", () => {
    renderPdfPage(state.pdf.pageNumber, { forceOcr: true });
  });
  document
    .getElementById("btn-pdf-analyze-all")
    .addEventListener("click", () => analyzeWholePdf());
  document.getElementById("chk-show-boxes").addEventListener("change", (e) => {
    layer.classList.toggle("show-boxes", e.target.checked);
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (state.activeMode !== "pdf" || !state.pdf.fitWidth || !state.pdf.doc) return;
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => renderPdfPage(state.pdf.pageNumber), 250);
  });
}

function bindLibraryEvents() {
  document.getElementById("file-upload-epub").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    await handleUploadSelection(file);
  });

  const searchInp = document.getElementById("inp-search-books");
  let searchTimer = null;
  searchInp.addEventListener("input", (e) => {
    if (searchTimer) clearTimeout(searchTimer);
    const value = e.target.value.trim();
    searchTimer = setTimeout(() => loadLibrary(value), 250);
  });
}

function bindModalEvents() {
  document.getElementById("btn-open-mining-modal").addEventListener("click", async () => {
    document.getElementById("modal-mining").classList.remove("hidden");
    await loadMinedCardsModal();
  });
  document.getElementById("btn-close-mining-modal").addEventListener("click", () => {
    document.getElementById("modal-mining").classList.add("hidden");
  });
  document.getElementById("btn-sync-ankiconnect").addEventListener("click", async () => {
    const btn = document.getElementById("btn-sync-ankiconnect");
    btn.textContent = "⏳ Syncing...";
    await fetch("/api/anki/sync-server", { method: "POST" });
    await refreshAnkiBadgeAndTryAutoFlush();
    await loadMinedCardsModal();
    btn.textContent = "🔄 Push to AnkiConnect";
  });

  document.getElementById("btn-sync-anki-snapshot").addEventListener("click", async () => {
    const btn = document.getElementById("btn-sync-anki-snapshot");
    btn.textContent = "⏳ Reading snapshot...";
    await triggerAnkiSnapshotSync();
    btn.textContent = "✅ Known Words Synced";
  });

  document.getElementById("btn-sync-snapshot-inline").addEventListener("click", async () => {
    await triggerAnkiSnapshotSync();
    if (state.currentBook) await loadBookCandidates(state.currentBook.book_id);
  });

  document.getElementById("btn-open-settings").addEventListener("click", () => {
    document.getElementById("modal-settings").classList.remove("hidden");
    loadOcrStatus();
  });
  document.getElementById("btn-close-settings").addEventListener("click", () => {
    document.getElementById("modal-settings").classList.add("hidden");
  });

  document.querySelectorAll(".seg-btn[data-theme-val]").forEach((btn) => {
    btn.addEventListener("click", () => applyTheme(btn.dataset.themeVal));
  });
  document.getElementById("rng-font-size").addEventListener("input", (e) => {
    applyFontSize(e.target.value);
  });
  document.getElementById("sel-font-family").addEventListener("change", (e) => {
    applyFontFamily(e.target.value);
  });

  document.getElementById("btn-save-settings").addEventListener("click", async () => {
    const deckName = document.getElementById("inp-deck-name").value.trim() || "Data Mining";
    const modelName =
      document.getElementById("inp-model-name").value.trim() || "YomiCalibre Mining";
    const ankiUrl =
      document.getElementById("inp-ankiconnect-url").value.trim() ||
      "http://127.0.0.1:8765";
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anki_deck_name: deckName,
        anki_model_name: modelName,
        ankiconnect_url: ankiUrl,
      }),
    });
    state.settings = await res.json();
    document.getElementById("modal-deck-name").textContent = deckName;
    document.getElementById("modal-settings").classList.add("hidden");
  });

  document.getElementById("btn-build-jmdict").addEventListener("click", async () => {
    const btn = document.getElementById("btn-build-jmdict");
    btn.disabled = true;
    btn.textContent = "⏳ Indexing JMdict in background...";
    await fetch("/api/dictionary/build-full", { method: "POST" });
  });

  document.querySelectorAll(".modal-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) backdrop.classList.add("hidden");
    });
  });
}

function bindGlobalKeyboard() {
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    const openModal = document.querySelector(".modal-backdrop:not(.hidden)");
    if (e.key === "Escape") {
      if (openModal) openModal.classList.add("hidden");
      else closeInspectorDock();
      return;
    }
    if (openModal || !state.currentBook) return;

    const back = e.key === "ArrowLeft" || e.key === "PageUp";
    const forward = e.key === "ArrowRight" || e.key === "PageDown";
    if (!back && !forward) return;

    if (state.activeMode === "pdf") {
      e.preventDefault();
      goToPdfPage(back ? -1 : 1);
    } else if (state.activeMode === "reader") {
      e.preventDefault();
      goToSection(back ? -1 : 1);
    }
  });
}

/** Horizontal swipe on a scroll container → previous/next section or page. */
function attachSwipeNavigation(element, onSwipe) {
  let startX = 0;
  let startY = 0;
  let tracking = false;

  element.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
    },
    { passive: true }
  );

  element.addEventListener(
    "touchend",
    (e) => {
      if (!tracking) return;
      tracking = false;
      const touch = e.changedTouches[0];
      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;
      if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.8) return;
      onSwipe(dx < 0 ? 1 : -1);
    },
    { passive: true }
  );
}

// --- Mining queue modal ----------------------------------------------------

async function loadMinedCardsModal() {
  const listEl = document.getElementById("mined-cards-list");
  listEl.innerHTML = `<p class="subtitle">Loading mined cards...</p>`;
  try {
    const res = await fetch("/api/anki/cards");
    const data = await res.json();
    const cards = data.cards || [];
    updateUnsyncedBadge(data.unsynced_count || 0);

    if (!cards.length) {
      listEl.innerHTML = `<p class="subtitle">No cards mined yet. Click any word in the reader, in the PDF view, or in the i+1 tab to generate a card.</p>`;
      return;
    }

    listEl.innerHTML = "";
    for (const c of cards) {
      const item = document.createElement("div");
      item.className = "mined-card-item";
      const frDefs = (c.definitions_fr || []).join(" ; ");
      const enDefs = (c.definitions_en || []).join(" ; ");
      const syncTag = c.anki_synced
        ? `<span class="anki-status-pill">✅ Synced to Anki</span>`
        : `<span class="insp-pos-badge">⏳ Queued (GUID: ${escapeHtml(c.guid || "")})</span>`;

      item.innerHTML = `
        <div class="mined-card-top">
          <div>
            <span class="mined-card-word">${escapeHtml(c.word_base)}</span>
            <span class="mined-card-reading">【${escapeHtml(c.reading_hiragana)} / ${escapeHtml(
        c.reading_katakana
      )}】</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            ${syncTag}
            <button class="btn-close-dock btn-del-card" data-cid="${c.id}" title="Delete">✕</button>
          </div>
        </div>
        <div class="mined-card-sentence">${c.sentence_html}</div>
        <div class="mined-card-defs">
          ${frDefs ? `<div>🇫🇷 ${escapeHtml(frDefs)}</div>` : ""}
          ${enDefs ? `<div>🇬🇧 ${escapeHtml(enDefs)}</div>` : ""}
        </div>
      `;
      item.querySelector(".btn-del-card").addEventListener("click", async () => {
        await fetch(`/api/anki/cards/${c.id}`, { method: "DELETE" });
        await loadMinedCardsModal();
      });
      listEl.appendChild(item);
    }
  } catch (err) {
    listEl.innerHTML = `<p class="subtitle">Failed to load cards.</p>`;
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
