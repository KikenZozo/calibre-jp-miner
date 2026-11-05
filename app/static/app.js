/**
 * YomiCalibre — Frontend Application
 * Supports both Interactive Pre-Segmented Reading (with fixed bottom panel)
 * and Batch i+1 Sentence Mining over cached books.
 */

const state = {
  books: [],
  currentBook: null,
  currentChapterIndex: 0,
  chapterData: null,
  sentencesMap: {},
  lastClickedPayload: null,
  activeMode: "reader", // 'reader' | 'candidates'
  settings: {
    anki_deck_name: "Data Mining",
    anki_model_name: "YomiCalibre Mining",
    ankiconnect_url: "http://127.0.0.1:8765",
    auto_mine_on_click: true,
  },
};

document.addEventListener("DOMContentLoaded", async () => {
  initLocalPreferences();
  bindUIEvents();
  await loadSettings();
  await loadLibrary();
  await refreshAnkiBadgeAndTryAutoFlush();
});

function initLocalPreferences() {
  const theme = localStorage.getItem("yc_theme") || "dark";
  const fontSize = localStorage.getItem("yc_font_size") || "20";
  const fontFamily = localStorage.getItem("yc_font_family") || "mincho";

  applyTheme(theme);
  applyFontSize(fontSize);
  applyFontFamily(fontFamily);
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
    document.getElementById("chk-auto-mine").checked = !!data.auto_mine_on_click;
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
    if (data.dictionary_stats) {
      updateDictStatsLabel(data.dictionary_stats);
    }
  } catch (err) {
    console.error("Failed to load settings:", err);
  }
}

function updateDictStatsLabel(stats) {
  const lbl = document.getElementById("dict-stats-label");
  if (!lbl) return;
  if (stats.is_full_jmdict) {
    lbl.textContent = `✅ Full JMdict active (${stats.total_entries.toLocaleString()} entries)`;
  } else {
    lbl.textContent = `⚡ Core dictionary active (${stats.total_entries} entries)`;
  }
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "Size unknown";
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

async function loadLibrary(query = "") {
  const grid = document.getElementById("books-grid");
  grid.innerHTML = `<p class="subtitle">Reading metadata.db index (0 Drive bytes accessed)...</p>`;
  try {
    const url = query
      ? `/api/books?q=${encodeURIComponent(query)}`
      : "/api/books";
    const res = await fetch(url);
    const data = await res.json();
    state.books = data.books || [];
    if (typeof data.known_words_count === "number") {
      document.getElementById("lbl-known-count").textContent =
        data.known_words_count.toLocaleString();
    }
    renderLibraryGrid();
  } catch (err) {
    grid.innerHTML = `<p class="subtitle">Error loading library index.</p>`;
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

    const fmtBadge = `<span class="meta-chip">${escapeHtml(book.preferred_format || "EPUB")}</span>`;
    const sizeBadge = `<span class="meta-chip">${escapeHtml(formatBytes(book.uncompressed_size))}</span>`;
    const symlinkBadge = book.is_symlink
      ? `<span class="meta-chip" title="Symlink into /home/enzo/calibre/gdrive">☁️ Drive Symlink</span>`
      : `<span class="meta-chip">💾 Local Disk</span>`;
    const cacheBadge = book.is_extracted
      ? `<span class="meta-chip cached">✅ Cached (${book.total_sentences} sents)</span>`
      : `<span class="meta-chip">⏳ Extract on open</span>`;

    card.innerHTML = `
      <div>
        <div class="book-card-title">${escapeHtml(book.title)}</div>
        <div class="book-card-author">${escapeHtml(book.author)}</div>
        <div class="book-meta-badges">
          ${fmtBadge}
          ${sizeBadge}
          ${symlinkBadge}
          ${cacheBadge}
        </div>
      </div>
      <div class="book-card-footer">
        <button class="btn-secondary btn-open-reader" style="flex:1;justify-content:center;">
          📖 Read
        </button>
        <button class="btn-primary btn-open-candidates" style="flex:1;justify-content:center;">
          🎯 Mine i+1
        </button>
      </div>
    `;

    card.querySelector(".btn-open-reader").addEventListener("click", () => {
      openBook(book, "reader");
    });
    card.querySelector(".btn-open-candidates").addEventListener("click", () => {
      openBook(book, "candidates");
    });

    grid.appendChild(card);
  }
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

  const breadcrumb = document.getElementById("current-book-label");
  breadcrumb.textContent = book.title;
  breadcrumb.classList.remove("hidden");

  closeInspectorDock();
  await switchBookMode(initialMode, targetChapter, book.progress?.scroll_ratio || 0);
}

async function switchBookMode(mode, chapterIndex = null, restoreScrollRatio = 0) {
  state.activeMode = mode;
  document.getElementById("tab-mode-reader").classList.toggle("active", mode === "reader");
  document
    .getElementById("tab-mode-candidates")
    .classList.toggle("active", mode === "candidates");

  if (mode === "reader") {
    document.getElementById("view-candidates").classList.add("hidden");
    document.getElementById("view-reader").classList.remove("hidden");
    document.getElementById("chapter-nav-controls").classList.remove("hidden");
    const targetSec =
      chapterIndex !== null ? chapterIndex : state.currentChapterIndex || 0;
    await loadChapter(state.currentBook.book_id, targetSec, restoreScrollRatio);
  } else {
    closeInspectorDock();
    document.getElementById("view-reader").classList.add("hidden");
    document.getElementById("chapter-nav-controls").classList.add("hidden");
    document.getElementById("view-candidates").classList.remove("hidden");
    await loadBookCandidates(state.currentBook.book_id);
  }
}

async function loadChapter(bookId, chapterIndex, restoreScrollRatio = 0) {
  const article = document.getElementById("reader-content");
  article.innerHTML = `<p class="subtitle">Extracting & loading permanent cache from mining.db...</p>`;
  closeInspectorDock();

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(bookId)}/chapter/${chapterIndex}`);
    if (!res.ok) throw new Error("Failed to load section");
    const data = await res.json();

    state.currentChapterIndex = data.chapter_index;
    state.chapterData = data;
    state.sentencesMap = data.sentences || {};

    populateChapterSelect(data.chapters || [], data.chapter_index);
    renderTokenizedChapter(data);

    const viewport = document.getElementById("reader-viewport");
    if (restoreScrollRatio > 0) {
      requestAnimationFrame(() => {
        const maxScroll = viewport.scrollHeight - viewport.clientHeight;
        if (maxScroll > 0) {
          viewport.scrollTop = Math.round(maxScroll * restoreScrollRatio);
        }
      });
    } else {
      viewport.scrollTop = 0;
    }

    saveCurrentProgress();
  } catch (err) {
    article.innerHTML = `<p class="subtitle">Could not extract or load this section.</p>`;
  }
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

  document.getElementById("btn-prev-chap").disabled = currentIndex <= 0;
  document.getElementById("btn-next-chap").disabled = currentIndex >= chapters.length - 1;
  document.getElementById("btn-footer-prev").disabled = currentIndex <= 0;
  document.getElementById("btn-footer-next").disabled = currentIndex >= chapters.length - 1;
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
          wordSpan.dataset.wid = tok.wid;
          wordSpan.dataset.sid = sent.sid;
          wordSpan.dataset.surface = tok.surface;
          wordSpan.dataset.base = tok.base || tok.surface;
          wordSpan.dataset.normalized = tok.normalized || "";
          wordSpan.dataset.hira = tok.reading_hiragana || "";
          wordSpan.dataset.kata = tok.reading_katakana || "";
          wordSpan.dataset.surfHira = tok.surface_hiragana || "";
          wordSpan.dataset.surfKata = tok.surface_katakana || "";
          wordSpan.dataset.posDetail = tok.pos_detail || "";
          wordSpan.dataset.freq = tok.frequency_rank || "";
          sentSpan.appendChild(wordSpan);
        }
      }
      blockEl.appendChild(sentSpan);
    }

    frag.appendChild(blockEl);
  }

  article.appendChild(frag);
}

// --- Batch i+1 Candidate Sentences Mode ---
async function loadBookCandidates(bookId) {
  const listEl = document.getElementById("candidates-list");
  const subEl = document.getElementById("candidates-subtitle");
  listEl.innerHTML = `<p class="subtitle">Computing i+1 sentence candidates against your known vocabulary...</p>`;

  try {
    const res = await fetch(`/api/books/${encodeURIComponent(bookId)}/candidates?limit=80`);
    const data = await res.json();
    const cands = data.candidates || [];

    subEl.textContent = `Book sentences: ${data.total_book_sentences} · Known words: ${data.known_words_count} · Top ${cands.length} candidates ranked by frequency`;

    if (!cands.length) {
      listEl.innerHTML = `<p class="subtitle">No candidate sentences found.</p>`;
      return;
    }

    listEl.innerHTML = "";
    for (const cand of cands) {
      const item = document.createElement("div");
      item.className = "mined-card-item";
      const frDefs = (cand.definitions_fr || []).join(" ; ");
      const enDefs = (cand.definitions_en || []).join(" ; ");

      item.innerHTML = `
        <div class="mined-card-top">
          <div>
            <span class="mined-card-word">${escapeHtml(cand.base)}</span>
            <span class="mined-card-reading">【${escapeHtml(cand.reading_hiragana)} / ${escapeHtml(cand.reading_katakana)}】</span>
            <span class="insp-pos-badge">${escapeHtml(cand.pos_detail)}</span>
            <span class="insp-freq-badge">Freq #${cand.frequency_rank} · ${cand.book_occurrences}× in book</span>
          </div>
          <div>
            <button class="btn-primary btn-mine-cand">➕ Mine to Anki</button>
          </div>
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
        const payload = {
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
        };
        const r = await fetch("/api/word/click", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
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
      });

      listEl.appendChild(item);
    }
  } catch (err) {
    listEl.innerHTML = `<p class="subtitle">Error computing i+1 candidates.</p>`;
  }
}

// --- Interactive Word Click Handler ---
async function handleWordClick(wordEl) {
  document.querySelectorAll(".jp-word.active-word").forEach((el) => {
    el.classList.remove("active-word");
  });
  document.querySelectorAll(".reader-sentence.active-sentence").forEach((el) => {
    el.classList.remove("active-sentence");
  });

  wordEl.classList.add("active-word");
  const parentSentEl = wordEl.closest(".reader-sentence");
  if (parentSentEl) {
    parentSentEl.classList.add("active-sentence");
  }

  const sid = wordEl.dataset.sid;
  const wid = wordEl.dataset.wid;
  const sentenceObj = state.sentencesMap[sid];
  const sentenceTokens = sentenceObj ? sentenceObj.tokens : [];

  const payload = {
    book_id: state.currentBook ? state.currentBook.book_id : "",
    book_title: state.currentBook ? state.currentBook.title : "",
    chapter_title: state.chapterData ? state.chapterData.chapter_title : "",
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

  requestAnimationFrame(() => {
    ensureWordVisibleInViewport(wordEl);
  });

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
          document.getElementById("insp-anki-status").textContent =
            `✅ Added to Anki (${state.settings.anki_deck_name})`;
        }
      }
      await refreshAnkiBadgeAndTryAutoFlush();
    }
  } catch (err) {
    console.error("Word lookup failed:", err);
  }
}

function openInspectorDockLoading(payload) {
  const dock = document.getElementById("bottom-inspector-dock");
  dock.classList.remove("hidden");

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
  document.getElementById("insp-definitions").innerHTML = `<span class="def-line-en">Looking up definition...</span>`;
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

  const defsBox = document.getElementById("insp-definitions");
  const frList = result.definitions_fr || [];
  const enList = result.definitions_en || [];

  let defsHtml = "";
  if (frList.length > 0) {
    defsHtml += `<div class="def-line-fr">🇫🇷 ${escapeHtml(frList.join(" ; "))}</div>`;
  }
  if (enList.length > 0) {
    defsHtml += `<div class="def-line-en">🇬🇧 ${escapeHtml(enList.join(" ; "))}</div>`;
  }
  if (!frList.length && !enList.length) {
    defsHtml = `<div class="def-line-en">Inflected / Proper term (${escapeHtml(result.surface_hiragana || result.reading_hiragana)})</div>`;
  }
  defsBox.innerHTML = defsHtml;

  document.getElementById("insp-sentence-html").innerHTML = result.sentence_html || "";

  const statusEl = document.getElementById("insp-anki-status");
  const manualBtn = document.getElementById("btn-manual-mine");
  if (result.mined_card) {
    manualBtn.classList.add("hidden");
    statusEl.classList.remove("hidden");
    if (result.mined_card.anki_synced) {
      statusEl.textContent = `✅ Added to Anki (${state.settings.anki_deck_name})`;
    } else {
      statusEl.textContent = `📥 Queued in '${state.settings.anki_deck_name}'`;
    }
  } else {
    statusEl.classList.add("hidden");
    manualBtn.classList.remove("hidden");
  }
}

function closeInspectorDock() {
  const dock = document.getElementById("bottom-inspector-dock");
  if (dock) dock.classList.add("hidden");
  document.querySelectorAll(".jp-word.active-word").forEach((el) => {
    el.classList.remove("active-word");
  });
  document.querySelectorAll(".reader-sentence.active-sentence").forEach((el) => {
    el.classList.remove("active-sentence");
  });
}

function ensureWordVisibleInViewport(wordEl) {
  const viewport = document.getElementById("reader-viewport");
  const vpRect = viewport.getBoundingClientRect();
  const wordRect = wordEl.getBoundingClientRect();

  if (wordRect.bottom > vpRect.bottom - 24) {
    const delta = wordRect.bottom - (vpRect.bottom - 48);
    viewport.scrollBy({ top: delta, behavior: "smooth" });
  }
}

// --- Browser-to-AnkiConnect Bridge (Windows PC) ---
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
      if (flushedCount > 0) {
        updateUnsyncedBadge(unsyncedCards.length - flushedCount);
      }
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
  const viewport = document.getElementById("reader-viewport");
  const maxScroll = viewport.scrollHeight - viewport.clientHeight;
  const ratio = maxScroll > 0 ? viewport.scrollTop / maxScroll : 0;

  fetch(`/api/books/${encodeURIComponent(state.currentBook.book_id)}/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      book_title: state.currentBook.title,
      chapter_index: state.currentChapterIndex,
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

function bindUIEvents() {
  const readerViewport = document.getElementById("reader-viewport");
  readerViewport.addEventListener("click", (e) => {
    const wordEl = e.target.closest(".jp-word");
    if (wordEl) {
      e.stopPropagation();
      handleWordClick(wordEl);
    } else if (!e.target.closest(".chapter-footer-nav")) {
      closeInspectorDock();
    }
  });

  let scrollTimer = null;
  readerViewport.addEventListener("scroll", () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(saveCurrentProgress, 600);
  });

  document.getElementById("btn-close-inspector").addEventListener("click", closeInspectorDock);

  document.getElementById("btn-manual-mine").addEventListener("click", async () => {
    if (!state.lastClickedPayload) return;
    const payload = { ...state.lastClickedPayload, force_mine: true };
    const res = await fetch("/api/word/click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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

  document.getElementById("tab-mode-reader").addEventListener("click", () => {
    if (state.currentBook) switchBookMode("reader");
  });
  document.getElementById("tab-mode-candidates").addEventListener("click", () => {
    if (state.currentBook) switchBookMode("candidates");
  });

  document.getElementById("btn-back-library").addEventListener("click", async () => {
    closeInspectorDock();
    document.getElementById("view-reader").classList.add("hidden");
    document.getElementById("view-candidates").classList.add("hidden");
    document.getElementById("view-library").classList.remove("hidden");
    document.getElementById("btn-back-library").classList.add("hidden");
    document.getElementById("book-mode-tabs").classList.add("hidden");
    document.getElementById("chapter-nav-controls").classList.add("hidden");
    document.getElementById("current-book-label").classList.add("hidden");
    await loadLibrary();
  });

  document.getElementById("select-chapter").addEventListener("change", (e) => {
    if (!state.currentBook) return;
    loadChapter(state.currentBook.book_id, parseInt(e.target.value, 10));
  });
  document.getElementById("btn-prev-chap").addEventListener("click", () => {
    if (state.currentBook && state.currentChapterIndex > 0) {
      loadChapter(state.currentBook.book_id, state.currentChapterIndex - 1);
    }
  });
  document.getElementById("btn-next-chap").addEventListener("click", () => {
    if (state.currentBook) {
      loadChapter(state.currentBook.book_id, state.currentChapterIndex + 1);
    }
  });
  document.getElementById("btn-footer-prev").addEventListener("click", () => {
    if (state.currentBook && state.currentChapterIndex > 0) {
      loadChapter(state.currentBook.book_id, state.currentChapterIndex - 1);
    }
  });
  document.getElementById("btn-footer-next").addEventListener("click", () => {
    if (state.currentBook) {
      loadChapter(state.currentBook.book_id, state.currentChapterIndex + 1);
    }
  });

  document.getElementById("chk-auto-mine").addEventListener("change", async (e) => {
    const val = e.target.checked;
    state.settings.auto_mine_on_click = val;
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_mine_on_click: val }),
    });
  });

  document.getElementById("file-upload-epub").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/books/upload", {
      method: "POST",
      body: formData,
    });
    if (res.ok) {
      await loadLibrary();
    }
    e.target.value = "";
  });

  const searchInp = document.getElementById("inp-search-books");
  if (searchInp) {
    let searchTimer = null;
    searchInp.addEventListener("input", (e) => {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        loadLibrary(e.target.value.trim());
      }, 250);
    });
  }

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
    if (state.currentBook) {
      await loadBookCandidates(state.currentBook.book_id);
    }
  });

  document.getElementById("btn-open-settings").addEventListener("click", () => {
    document.getElementById("modal-settings").classList.remove("hidden");
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
    const updated = await res.json();
    state.settings = updated;
    document.getElementById("modal-deck-name").textContent = deckName;
    document.getElementById("modal-settings").classList.add("hidden");
  });

  document.getElementById("btn-build-jmdict").addEventListener("click", async () => {
    const btn = document.getElementById("btn-build-jmdict");
    btn.disabled = true;
    btn.textContent = "⏳ Indexing JMdict in background...";
    await fetch("/api/dictionary/build-full", { method: "POST" });
  });
}

async function loadMinedCardsModal() {
  const listEl = document.getElementById("mined-cards-list");
  listEl.innerHTML = `<p class="subtitle">Loading mined cards...</p>`;
  try {
    const res = await fetch("/api/anki/cards");
    const data = await res.json();
    const cards = data.cards || [];
    updateUnsyncedBadge(data.unsynced_count || 0);

    if (!cards.length) {
      listEl.innerHTML = `<p class="subtitle">No cards mined yet. Click any word in the reader or in the i+1 tab to generate a card.</p>`;
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
            <span class="mined-card-reading">【${escapeHtml(c.reading_hiragana)} / ${escapeHtml(c.reading_katakana)}】</span>
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
