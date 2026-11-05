/**
 * YomiCalibre — Logique Frontend (Lecteur pré-découpé sans pop-up flottante + Auto-Minage Anki)
 */

const state = {
  books: [],
  currentBook: null,
  currentChapterIndex: 0,
  chapterData: null,
  sentencesMap: {},
  lastClickedPayload: null,
  settings: {
    anki_deck_name: "Data Mining",
    anki_model_name: "YomiCalibre Mining",
    ankiconnect_url: "http://127.0.0.1:8765",
    auto_mine_on_click: true,
  },
};

// --- Initialisation ---
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

// --- Chargement des paramètres et de la bibliothèque Calibre ---
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    state.settings = data;
    document.getElementById("chk-auto-mine").checked = !!data.auto_mine_on_click;
    document.getElementById("inp-deck-name").value = data.anki_deck_name || "Data Mining";
    document.getElementById("inp-ankiconnect-url").value =
      data.ankiconnect_url || "http://127.0.0.1:8765";
    document.getElementById("modal-deck-name").textContent =
      data.anki_deck_name || "Data Mining";

    if (data.dictionary_stats) {
      updateDictStatsLabel(data.dictionary_stats);
    }
  } catch (e) {
    console.error("Erreur chargement réglages:", e);
  }
}

function updateDictStatsLabel(stats) {
  const lbl = document.getElementById("dict-stats-label");
  if (!lbl) return;
  if (stats.is_full_jmdict) {
    lbl.textContent = `✅ Dictionnaire JMdict complet actif (${stats.total_entries.toLocaleString()} entrées FR/EN)`;
  } else {
    lbl.textContent = `⚡ Dictionnaire de base actif (${stats.total_entries} entrées FR/EN)`;
  }
}

async function loadLibrary(query = "") {
  const grid = document.getElementById("books-grid");
  grid.innerHTML = `<p class="subtitle">Lecture de l'index léger metadata.db (aucun téléchargement Drive)...</p>`;
  try {
    const url = query
      ? `/api/books?q=${encodeURIComponent(query)}`
      : "/api/books";
    const res = await fetch(url);
    const data = await res.json();
    state.books = data.books || [];
    renderLibraryGrid();
  } catch (e) {
    grid.innerHTML = `<p class="subtitle">Erreur lors du chargement de la bibliothèque.</p>`;
  }
}

function renderLibraryGrid() {
  const grid = document.getElementById("books-grid");
  if (!state.books.length) {
    grid.innerHTML = `<p class="subtitle">Aucun livre trouvé. Placez vos EPUBs dans votre bibliothèque Calibre ou cliquez sur « Importer un .epub ».</p>`;
    return;
  }

  grid.innerHTML = "";
  for (const book of state.books) {
    const card = document.createElement("div");
    card.className = "book-card";

    const prog = book.progress;
    const progText = prog
      ? `Chapitre ${(prog.chapter_index || 0) + 1} · En cours`
      : "Nouveau";

    card.innerHTML = `
      <div>
        <div class="book-card-title">${escapeHtml(book.title)}</div>
        <div class="book-card-author">${escapeHtml(book.author)}</div>
      </div>
      <div class="book-card-footer">
        <span>${escapeHtml(book.source || "Calibre")}</span>
        <span class="progress-tag">${escapeHtml(progText)}</span>
      </div>
    `;
    card.addEventListener("click", () => openBook(book));
    grid.appendChild(card);
  }
}

// --- Ouverture d'un livre et rendu des chapitres pré-découpés ---
async function openBook(book, chapterIndex = null) {
  state.currentBook = book;
  const targetChapter =
    chapterIndex !== null
      ? chapterIndex
      : book.progress
      ? book.progress.chapter_index || 0
      : 0;

  document.getElementById("view-library").classList.add("hidden");
  document.getElementById("view-reader").classList.remove("hidden");
  document.getElementById("btn-back-library").classList.remove("hidden");
  document.getElementById("chapter-nav-controls").classList.remove("hidden");

  const breadcrumb = document.getElementById("current-book-label");
  breadcrumb.textContent = book.title;
  breadcrumb.classList.remove("hidden");

  closeInspectorDock();
  await loadChapter(book.book_id, targetChapter, book.progress?.scroll_ratio || 0);
}

async function loadChapter(bookId, chapterIndex, restoreScrollRatio = 0) {
  const article = document.getElementById("reader-content");
  article.innerHTML = `<p class="subtitle">Analyse morphologique et découpage des phrases en cours...</p>`;
  closeInspectorDock();

  try {
    const res = await fetch(`/api/books/${bookId}/chapter/${chapterIndex}`);
    if (!res.ok) throw new Error("Erreur chargement chapitre");
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
  } catch (e) {
    article.innerHTML = `<p class="subtitle">Impossible de charger ce chapitre.</p>`;
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
          sentSpan.appendChild(wordSpan);
        }
      }
      blockEl.appendChild(sentSpan);
    }

    frag.appendChild(blockEl);
  }

  article.appendChild(frag);
}

// --- Clic sur un mot prédécoupé : Affichage dans le panneau fixe bas + Création Carte Anki ---
async function handleWordClick(wordEl) {
  // 1. Nettoyer la sélection précédente
  document.querySelectorAll(".jp-word.active-word").forEach((el) => {
    el.classList.remove("active-word");
  });
  document.querySelectorAll(".reader-sentence.active-sentence").forEach((el) => {
    el.classList.remove("active-sentence");
  });

  // 2. Marquer uniquement le mot cliqué et sa phrase
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

  // 3. Ouvrir le panneau fixe intégré en bas (sans jamais survoler le texte)
  openInspectorDockLoading(payload);

  // S'assurer que la ligne cliquée reste bien visible au-dessus du panneau bas
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

    // Si une carte Anki a été créée et n'a pas encore été poussée par le serveur vers AnkiConnect,
    // on tente aussi de l'envoyer directement depuis le navigateur vers AnkiConnect local (127.0.0.1:8765)
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
            `✅ Carte Anki créée (${state.settings.anki_deck_name})`;
        }
      }
      await refreshAnkiBadgeAndTryAutoFlush();
    }
  } catch (e) {
    console.error("Erreur lors de la consultation du mot:", e);
  }
}

function openInspectorDockLoading(payload) {
  const dock = document.getElementById("bottom-inspector-dock");
  dock.classList.remove("hidden");

  document.getElementById("insp-word-base").textContent = payload.base || payload.surface;
  const surfEl = document.getElementById("insp-word-surface");
  if (payload.surface && payload.surface !== payload.base) {
    surfEl.textContent = `dans le texte : ${payload.surface}`;
    surfEl.classList.remove("hidden");
  } else {
    surfEl.classList.add("hidden");
  }

  document.getElementById("insp-pos").textContent = payload.pos_detail || "Mot";
  document.getElementById("insp-hira").textContent = payload.reading_hiragana || "...";
  document.getElementById("insp-kata").textContent = payload.reading_katakana || "...";
  document.getElementById("insp-definitions").innerHTML = `<span class="def-line-en">Recherche de la définition...</span>`;
  document.getElementById("insp-anki-status").textContent = "⏳...";
}

function renderInspectorDockResult(result) {
  document.getElementById("insp-word-base").textContent = result.base || result.surface;
  document.getElementById("insp-hira").textContent = result.reading_hiragana || "";
  document.getElementById("insp-kata").textContent = result.reading_katakana || "";
  document.getElementById("insp-pos").textContent = result.pos_detail || "Mot";

  // Définitions Français + Anglais
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
    defsHtml = `<div class="def-line-en">Terme propre / Forme conjuguée (${escapeHtml(result.surface_hiragana || result.reading_hiragana)})</div>`;
  }
  defsBox.innerHTML = defsHtml;

  // Phrase de contexte avec le mot souligné (<u>mot</u>)
  document.getElementById("insp-sentence-html").innerHTML = result.sentence_html || "";

  // Statut Anki
  const statusEl = document.getElementById("insp-anki-status");
  const manualBtn = document.getElementById("btn-manual-mine");
  if (result.mined_card) {
    manualBtn.classList.add("hidden");
    statusEl.classList.remove("hidden");
    if (result.mined_card.anki_synced) {
      statusEl.textContent = `✅ Carte Anki créée (${state.settings.anki_deck_name})`;
    } else {
      statusEl.textContent = `📥 Ajouté au deck « ${state.settings.anki_deck_name} »`;
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

  // Si le mot cliqué se retrouve trop bas (près du bord inférieur de la zone de lecture),
  // on ajuste le scroll de la zone de lecture pour garder la phrase bien au-dessus du panneau fixe.
  if (wordRect.bottom > vpRect.bottom - 24) {
    const delta = wordRect.bottom - (vpRect.bottom - 48);
    viewport.scrollBy({ top: delta, behavior: "smooth" });
  }
}

// --- Pont AnkiConnect Navigateur (pour synchroniser automatiquement quand le PC portable est ouvert) ---
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
      // 1. Créer le deck Data Mining
      await invokeAnkiConnectBrowser(url, "createDeck", { deck: deckName });

      // 2. Créer le modèle s'il n'existe pas
      const models = await invokeAnkiConnectBrowser(url, "modelNames", {});
      if (Array.isArray(models) && !models.includes(modelName) && modelSpec) {
        await invokeAnkiConnectBrowser(url, "createModel", modelSpec);
      }

      // 3. Ajouter la note
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
    } catch (_) {
      // Anki n'est pas ouvert sur cet appareil (ex: lecture sur téléphone), la carte reste bien au chaud sur le serveur
    }
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

    // Si des cartes minées sur le téléphone sont en attente et qu'Anki est ouvert sur ce PC,
    // on les pousse automatiquement vers Anki !
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

// --- Sauvegarde automatique de la progression ---
function saveCurrentProgress() {
  if (!state.currentBook) return;
  const viewport = document.getElementById("reader-viewport");
  const maxScroll = viewport.scrollHeight - viewport.clientHeight;
  const ratio = maxScroll > 0 ? viewport.scrollTop / maxScroll : 0;

  fetch(`/api/books/${state.currentBook.book_id}/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      book_title: state.currentBook.title,
      chapter_index: state.currentChapterIndex,
      scroll_ratio: Number(ratio.toFixed(4)),
    }),
  }).catch(() => {});
}

// --- Événements de l'interface ---
function bindUIEvents() {
  // Délégation de clic sur les mots prédécoupés dans le lecteur
  const readerViewport = document.getElementById("reader-viewport");
  readerViewport.addEventListener("click", (e) => {
    const wordEl = e.target.closest(".jp-word");
    if (wordEl) {
      e.stopPropagation();
      handleWordClick(wordEl);
    } else if (!e.target.closest(".chapter-footer-nav")) {
      // Clic dans la marge ou sur de la ponctuation : referme proprement le panneau du bas
      closeInspectorDock();
    }
  });

  // Sauvegarde du scroll à la lecture
  let scrollTimer = null;
  readerViewport.addEventListener("scroll", () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(saveCurrentProgress, 600);
  });

  // Fermer le panneau bas
  document.getElementById("btn-close-inspector").addEventListener("click", closeInspectorDock);

  // Bouton minage manuel (si Auto-Anki est décoché)
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

  // Retour à la bibliothèque Calibre
  document.getElementById("btn-back-library").addEventListener("click", async () => {
    closeInspectorDock();
    document.getElementById("view-reader").classList.add("hidden");
    document.getElementById("view-library").classList.remove("hidden");
    document.getElementById("btn-back-library").classList.add("hidden");
    document.getElementById("chapter-nav-controls").classList.add("hidden");
    document.getElementById("current-book-label").classList.add("hidden");
    await loadLibrary();
  });

  // Navigation entre chapitres
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

  // Toggle Auto-Mine au clic
  document.getElementById("chk-auto-mine").addEventListener("change", async (e) => {
    const val = e.target.checked;
    state.settings.auto_mine_on_click = val;
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_mine_on_click: val }),
    });
  });

  // Import d'un fichier .epub
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

  // Recherche SQL instantanée dans metadata.db (sans toucher aux fichiers EPUB sur le Drive)
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

  // Modale Deck Anki Data Mining
  document.getElementById("btn-open-mining-modal").addEventListener("click", async () => {
    document.getElementById("modal-mining").classList.remove("hidden");
    await loadMinedCardsModal();
  });
  document.getElementById("btn-close-mining-modal").addEventListener("click", () => {
    document.getElementById("modal-mining").classList.add("hidden");
  });
  document.getElementById("btn-sync-ankiconnect").addEventListener("click", async () => {
    const btn = document.getElementById("btn-sync-ankiconnect");
    btn.textContent = "⏳ Synchronisation...";
    await fetch("/api/anki/sync-server", { method: "POST" });
    await refreshAnkiBadgeAndTryAutoFlush();
    await loadMinedCardsModal();
    btn.textContent = "🔄 Synchroniser vers AnkiConnect";
  });

  // Modale Réglages
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
    const ankiUrl =
      document.getElementById("inp-ankiconnect-url").value.trim() ||
      "http://127.0.0.1:8765";
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anki_deck_name: deckName,
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
    btn.textContent = "⏳ Téléchargement et indexation en cours...";
    await fetch("/api/dictionary/build-full", { method: "POST" });
  });
}

async function loadMinedCardsModal() {
  const listEl = document.getElementById("mined-cards-list");
  listEl.innerHTML = `<p class="subtitle">Chargement des cartes minées...</p>`;
  try {
    const res = await fetch("/api/anki/cards");
    const data = await res.json();
    const cards = data.cards || [];
    updateUnsyncedBadge(data.unsynced_count || 0);

    if (!cards.length) {
      listEl.innerHTML = `<p class="subtitle">Aucune carte minée pour le moment. Cliquez sur un mot pendant votre lecture pour créer automatiquement une carte avec sa phrase soulignée.</p>`;
      return;
    }

    listEl.innerHTML = "";
    for (const c of cards) {
      const item = document.createElement("div");
      item.className = "mined-card-item";
      const frDefs = (c.definitions_fr || []).join(" ; ");
      const enDefs = (c.definitions_en || []).join(" ; ");
      const syncTag = c.anki_synced
        ? `<span class="anki-status-pill">✅ Synchronisé Anki</span>`
        : `<span class="insp-pos-badge">⏳ En file d'attente</span>`;

      item.innerHTML = `
        <div class="mined-card-top">
          <div>
            <span class="mined-card-word">${escapeHtml(c.word_base)}</span>
            <span class="mined-card-reading">【${escapeHtml(c.reading_hiragana)} / ${escapeHtml(c.reading_katakana)}】</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            ${syncTag}
            <button class="btn-close-dock btn-del-card" data-cid="${c.id}" title="Supprimer">✕</button>
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
  } catch (e) {
    listEl.innerHTML = `<p class="subtitle">Erreur lors du chargement des cartes.</p>`;
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
