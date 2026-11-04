/* QuizJet book reader: sentence-synced read-aloud, word/sentence lookup, a personal word list.
   Everything runs in the browser; nothing is sent to QuizJet.
   - Read-aloud: the browser's own speech engine (speechSynthesis), one utterance per sentence, so
     the highlighted sentence is always the one being spoken. Word highlight only when the engine
     reports word boundaries; otherwise sentence-level only (no fake precision).
   - Dictionary: dictionaryapi.dev (English, free, no key). Translation: MyMemory (free, no key,
     daily quota per visitor); on failure a Google Translate link is offered instead.
   - Reading position, settings and saved words: localStorage on this device. */
(function () {
  "use strict";
  const root = document.getElementById("reader");
  if (!root) return;
  const D = JSON.parse(document.getElementById("rd-data").textContent);
  const C = D.copy;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => "&#" + ch.charCodeAt(0) + ";");
  const text = $("rd-text"), chSel = $("rd-ch"), seek = $("rd-seek"), pos = $("rd-pos"), bar = $("rd-bar");
  const playBtn = $("rd-play"), speedBtn = $("rd-speed"), prevBtn = $("rd-prev"), nextBtn = $("rd-next");
  const panel = $("rd-panel"), panelBody = $("rd-panel-body"), setDlg = $("rd-set"), wordsDlg = $("rd-words");

  function load(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch (_) { return fallback; } }
  function store(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode: keep going */ } }

  const POS_KEY = "qj.book." + D.slug, PREFS_KEY = "qj.reader", WORDS_KEY = "qj.words";
  const RATES = [0.6, 0.8, 1, 1.2];
  const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
  const prefs = Object.assign({ font: 19, spacing: "1.8", theme: dark ? "dark" : "light", view: "book", rate: 1,
    target: D.lang === "en" ? "" : D.lang }, load(PREFS_KEY, {}));
  let words = load(WORDS_KEY, []);
  let chapter = 1, sentences = [], spans = [], active = -1, playing = false, continuous = false, run = 0, userScrollAt = 0;

  /* ---------- settings ---------- */
  function applyPrefs() {
    root.style.setProperty("--rd-font", prefs.font + "px");
    root.style.setProperty("--rd-lh", prefs.spacing);
    document.documentElement.setAttribute("data-rd-theme", prefs.theme);
    text.classList.toggle("study", prefs.view === "study");
    speedBtn.textContent = prefs.rate + "×";
    const form = $("rd-set-form");
    ["spacing", "theme", "view"].forEach((name) => form.querySelectorAll('[name="' + name + '"]').forEach((r) => { r.checked = r.value === String(prefs[name]); }));
    form.elements.target.value = prefs.target;
    store(PREFS_KEY, prefs);
  }
  $("rd-set-form").addEventListener("change", (ev) => { prefs[ev.target.name] = ev.target.value; applyPrefs(); });
  $("rd-set-form").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-font]");
    if (b) { prefs.font = Math.min(28, Math.max(14, prefs.font + 2 * Number(b.dataset.font))); applyPrefs(); }
  });
  $("rd-set-btn").addEventListener("click", () => setDlg.showModal());

  /* ---------- speech ---------- */
  const synth = window.speechSynthesis;
  let voice = null;
  function pickVoice() {
    const en = synth.getVoices().filter((v) => /^en([-_]|$)/i.test(v.lang));
    // Local voices first: Chrome's network voices stop mid-sentence after ~15 s.
    voice = en.find((v) => /GB/i.test(v.lang) && v.localService) || en.find((v) => v.localService) || en[0] || null;
  }
  if (synth && window.SpeechSynthesisUtterance) {
    pickVoice();
    synth.addEventListener ? synth.addEventListener("voiceschanged", pickVoice) : (synth.onvoiceschanged = pickVoice);
  } else {
    $("rd-player").hidden = true;
    $("rd-this").disabled = true;
    $("rd-novoice").hidden = false;
  }
  function utter(textToSay) {
    const u = new SpeechSynthesisUtterance(textToSay);
    u.lang = voice ? voice.lang : "en-GB";
    if (voice) u.voice = voice;
    u.rate = prefs.rate;
    return u;
  }
  function stop() {
    run++;
    if (synth) synth.cancel();
    playing = false;
    markWord(-1);
    updatePlay();
  }
  function speakFrom(i, all) {
    if (!synth || i < 0 || i >= sentences.length) return;
    const mine = ++run;
    synth.cancel();
    playing = true;
    continuous = all;
    setActive(i, 1);
    updatePlay();
    const u = utter(sentences[i]);
    u.onboundary = (ev) => { if (mine === run && ev.name !== "sentence") markWord(ev.charIndex); };
    u.onend = () => {
      if (mine !== run) return; // a newer command already took over
      markWord(-1);
      if (continuous && i + 1 < sentences.length) speakFrom(i + 1, true);
      else { playing = false; updatePlay(); }
    };
    u.onerror = () => { if (mine === run) { playing = false; updatePlay(); } };
    synth.speak(u);
  }
  function sayWord(w) { if (!synth) return; stop(); synth.speak(utter(w)); }
  function updatePlay() {
    playBtn.textContent = playing && continuous ? "❚❚" : "▶";
    playBtn.setAttribute("aria-label", playing && continuous ? C.bkPause : C.bkPlay);
  }
  let litWord = null;
  function markWord(charIndex) {
    if (litWord) litWord.classList.remove("on");
    litWord = null;
    if (charIndex < 0 || active < 0) return;
    for (const w of spans[active].querySelectorAll(".w")) {
      const o = Number(w.dataset.o);
      if (charIndex >= o && charIndex < o + w.textContent.length) { w.classList.add("on"); litWord = w; break; }
    }
  }

  /* ---------- position ---------- */
  function setActive(i, follow) {
    if (i < 0 || i >= spans.length) return;
    if (active >= 0 && spans[active]) spans[active].classList.remove("on");
    active = i;
    spans[i].classList.add("on");
    seek.value = i;
    pos.textContent = i + 1 + " / " + spans.length;
    prevBtn.disabled = i === 0;
    nextBtn.disabled = i === spans.length - 1;
    // follow: 0 = never scroll (the reader tapped it), 1 = only if the reader isn't scrolling by hand, 2 = always.
    if (follow === 2 || (follow === 1 && Date.now() - userScrollAt > 4000)) {
      const r = spans[i].getBoundingClientRect();
      if (r.top < 60 || r.bottom > innerHeight - bar.offsetHeight - 10) spans[i].scrollIntoView({ block: "center", behavior: "smooth" });
    }
    savePos();
  }
  function topSentence() {
    const limit = 70;
    for (let i = 0; i < spans.length; i++) if (spans[i].getBoundingClientRect().bottom > limit) return i;
    return 0;
  }
  function savePos() {
    if (!spans.length) return;
    const top = topSentence();
    store(POS_KEY, { ch: chapter, s: Math.max(active, 0), top: top });
    const pct = new Intl.NumberFormat(D.lang, { style: "percent" }).format(spans.length > 1 ? top / (spans.length - 1) : 1);
    $("rd-progress").textContent = C.bkChapter + " " + chapter + " / " + D.chapters.length + " · " + C.bkProgress + ": " + pct;
  }
  let scrollTimer = 0;
  addEventListener("scroll", () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(savePos, 400); }, { passive: true });
  ["wheel", "touchmove", "keydown"].forEach((t) => addEventListener(t, () => { userScrollAt = Date.now(); }, { passive: true }));

  /* ---------- chapter loading ---------- */
  const WORD_RE = /[A-Za-z]+(?:['’][A-Za-z]+)*/g;
  function render(data) {
    const frag = document.createDocumentFragment();
    sentences = [];
    spans = [];
    for (const para of data.p) {
      const p = document.createElement("p");
      for (const s of para) {
        const span = document.createElement("span");
        span.className = "s";
        span.dataset.i = sentences.length;
        let last = 0;
        for (const m of s.matchAll(WORD_RE)) {
          if (m.index > last) span.append(s.slice(last, m.index));
          const w = document.createElement("span");
          w.className = "w";
          w.dataset.o = m.index;
          w.textContent = m[0];
          span.append(w);
          last = m.index + m[0].length;
        }
        if (last < s.length) span.append(s.slice(last));
        span.insertAdjacentHTML("beforeend", '<button type="button" class="sb sb-play" aria-label="' + esc(C.bkThis) + '">▶</button>'
          + '<button type="button" class="sb sb-tr" aria-label="' + esc(C.bkTranslate) + '">文A</button>');
        p.append(span, " ");
        sentences.push(s);
        spans.push(span);
      }
      frag.append(p);
    }
    text.replaceChildren(frag);
    seek.max = Math.max(spans.length - 1, 0);
    markSaved();
  }
  function showQuiz() {
    const key = D.chapterQuizzes[String(chapter)] || D.quiz;
    $("rd-quiz-link").href = "https://t.me/" + D.bot + (key ? "?start=" + encodeURIComponent(key) : "");
    $("rd-next-ch").hidden = chapter >= D.chapters.length;
    $("rd-next-ch").href = "#" + (chapter + 1);
    $("rd-quiz").hidden = false;
  }
  function loadChapter(n) {
    stop();
    chapter = n;
    chSel.value = String(n);
    active = -1;
    sentences = [];
    spans = []; // savePos() is a no-op until the new chapter is on screen
    $("rd-quiz").hidden = true;
    text.innerHTML = "<p>" + esc(C.bkLoading) + "</p>";
    fetch("/kitaplar/" + D.slug + "/" + n + ".json")
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((data) => {
        if (n !== chapter) return;
        render(data);
        showQuiz();
        const saved = load(POS_KEY, {});
        if (saved.ch === n && saved.top > 0 && spans[saved.top]) spans[saved.top].scrollIntoView({ block: "start" });
        setActive(saved.ch === n && spans[saved.s] ? saved.s : 0, 0);
      })
      .catch(() => {
        if (n !== chapter) return;
        text.innerHTML = '<p>' + esc(C.bkError) + '</p><p><button type="button" class="button button-dark" id="rd-retry">' + esc(C.bkRetry) + "</button></p>";
        $("rd-retry").addEventListener("click", () => loadChapter(n));
      });
  }
  function chapterFromHash() {
    const n = parseInt(location.hash.slice(1), 10);
    return n >= 1 && n <= D.chapters.length ? n : 0;
  }
  chSel.addEventListener("change", () => { location.hash = chSel.value; });
  addEventListener("hashchange", () => {
    const n = chapterFromHash();
    if (n && n !== chapter) { loadChapter(n); root.scrollIntoView(); }
  });

  /* ---------- controls ---------- */
  function current() { return active >= 0 ? active : topSentence(); }
  prevBtn.addEventListener("click", () => step(-1));
  nextBtn.addEventListener("click", () => step(1));
  function step(d) {
    const i = Math.min(Math.max(current() + d, 0), spans.length - 1);
    if (i === active) return;
    setActive(i, 2);
    if (playing) speakFrom(i, continuous);
  }
  $("rd-this").addEventListener("click", () => speakFrom(current(), false));
  $("rd-tr").addEventListener("click", () => { setActive(current(), 0); openSentence(active); });
  playBtn.addEventListener("click", () => (playing && continuous ? stop() : speakFrom(current(), true)));
  speedBtn.addEventListener("click", () => {
    prefs.rate = RATES[(RATES.indexOf(prefs.rate) + 1) % RATES.length];
    applyPrefs();
    if (playing) speakFrom(active, continuous);
  });
  seek.addEventListener("input", () => setActive(Number(seek.value), 2));
  seek.addEventListener("change", () => { if (playing) speakFrom(Number(seek.value), continuous); });

  text.addEventListener("click", (ev) => {
    const s = ev.target.closest(".s");
    if (!s) return;
    const i = Number(s.dataset.i);
    const btn = ev.target.closest(".sb");
    if (btn) { setActive(i, 0); btn.classList.contains("sb-play") ? speakFrom(i, false) : openSentence(i); return; }
    const w = ev.target.closest(".w");
    if (w) { openWord(w, i); return; } // the word panel never moves the reading position
    setActive(i, 0);
    if (playing) speakFrom(i, continuous);
  });

  /* ---------- lookups ---------- */
  const trCache = new Map(), defCache = new Map();
  function googleUrl(q) {
    const tl = (prefs.target || "tr").replace(/-BR$/, "");
    return "https://translate.google.com/?sl=en&tl=" + encodeURIComponent(tl) + "&text=" + encodeURIComponent(q) + "&op=translate";
  }
  function translate(q) {
    const key = prefs.target + "|" + q;
    if (!trCache.has(key)) {
      // ponytail: MyMemory takes at most 500 bytes per request; longer sentences get only the Google link.
      const p = q.length > 480 ? Promise.reject(new Error("long")) :
        fetch("https://api.mymemory.translated.net/get?langpair=en%7C" + encodeURIComponent(prefs.target) + "&q=" + encodeURIComponent(q))
          .then((r) => r.json())
          .then((d) => {
            const t = d && d.responseData && d.responseData.translatedText;
            if (Number(d.responseStatus) !== 200 || !t || /MYMEMORY WARNING/i.test(t)) throw new Error("quota");
            return new DOMParser().parseFromString(t, "text/html").documentElement.textContent;
          });
      p.catch(() => trCache.delete(key));
      trCache.set(key, p);
    }
    return trCache.get(key);
  }
  function showTranslation(el, q) {
    if (!prefs.target) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = C.bkLoading;
    translate(q).then((t) => { el.textContent = t; }, () => {
      el.innerHTML = esc(C.bkTrError) + ' <a href="' + esc(googleUrl(q)) + '" target="_blank" rel="noopener noreferrer">' + esc(C.bkOpenGoogle) + "</a>";
    });
  }
  // ponytail: suffix stripping, not a lemmatiser — irregular forms (went, children) resolve only if the dictionary lists them.
  function baseForms(w) {
    const c = [w], add = (x) => { if (x.length > 1 && !c.includes(x)) c.push(x); };
    if (/'s$/.test(w)) add(w.slice(0, -2));
    if (/ies$|ied$/.test(w)) add(w.slice(0, -3) + "y");
    if (/(.)\1(ed|ing)$/.test(w)) add(w.replace(/(.)(ed|ing)$/, ""));
    if (/ing$/.test(w)) { add(w.slice(0, -3)); add(w.slice(0, -3) + "e"); }
    if (/ed$/.test(w)) { add(w.slice(0, -2)); add(w.slice(0, -1)); }
    if (/es$/.test(w)) add(w.slice(0, -2));
    if (/s$/.test(w)) add(w.slice(0, -1));
    if (/ly$/.test(w)) add(w.slice(0, -2));
    return c.slice(0, 4);
  }
  async function define(w) {
    if (defCache.has(w)) return defCache.get(w);
    let found = null, settled = true;
    for (const cand of baseForms(w)) {
      let r = null;
      try { r = await fetch("https://api.dictionaryapi.dev/api/v2/entries/en/" + encodeURIComponent(cand)); } catch (_) { settled = false; break; }
      if (r.ok) { found = { base: cand, entries: await r.json() }; break; }
      if (r.status !== 404) { settled = false; break; } // rate limit or outage: don't burn more requests
    }
    if (settled) defCache.set(w, found); // "not in the dictionary" is an answer; "offline" is not
    return found;
  }
  function renderDefinition(el, phonEl, w, res) {
    if (!res) { el.innerHTML = "<li>" + esc(C.bkNoDef) + "</li>"; return; }
    const phon = res.entries.map((x) => x.phonetic || (x.phonetics || []).map((p) => p.text).find(Boolean)).find(Boolean);
    phonEl.textContent = (phon || "") + (res.base !== w ? "  ·  " + C.bkBase + ": " + res.base : "");
    const items = [];
    for (const entry of res.entries) for (const m of entry.meanings || []) {
      for (const d of (m.definitions || []).slice(0, 2)) {
        items.push("<li><b>" + esc(m.partOfSpeech) + "</b> " + esc(d.definition) + (d.example ? "<br><em>“" + esc(d.example) + "”</em>" : "") + "</li>");
      }
      if (items.length >= 5) break;
    }
    el.innerHTML = items.slice(0, 5).join("");
  }

  /* ---------- panels ---------- */
  function quote(i, w) {
    const s = sentences[i];
    if (!w) return esc(s);
    const o = Number(w.dataset.o), len = w.textContent.length;
    return esc(s.slice(0, o)) + "<mark>" + esc(s.slice(o, o + len)) + "</mark>" + esc(s.slice(o + len));
  }
  function openWord(w, i) {
    stop();
    const word = w.textContent, lower = word.toLowerCase().replace(/’/g, "'");
    panelBody.innerHTML =
      '<h2 lang="en" dir="ltr">' + esc(word) + "</h2>"
      + '<p class="rd-phon" lang="en" dir="ltr" id="rd-phon"></p>'
      + '<p class="rd-trans" id="rd-wtr"></p>'
      + "<h3>" + esc(C.bkDict) + '</h3><ul class="rd-def" lang="en" dir="ltr" id="rd-def"><li>' + esc(C.bkLoading) + "</li></ul>"
      + "<h3>" + esc(C.bkContext) + '</h3><blockquote class="rd-quote" lang="en" dir="ltr">' + quote(i, w) + "</blockquote>"
      + '<p id="rd-str" hidden></p>'
      + '<div class="rd-actions">'
      + '<button type="button" class="rd-mini" data-act="say">🔊 ' + esc(C.bkListen) + "</button>"
      + '<button type="button" class="rd-mini" data-act="save" aria-pressed="false"></button>'
      + (prefs.target ? '<button type="button" class="rd-mini" data-act="str">文A ' + esc(C.bkTranslate) + "</button>" : "")
      + (synth ? '<button type="button" class="rd-mini" data-act="from">▶ ' + esc(C.bkFromHere) + "</button>" : "")
      + "</div>";
    const ctx = { word: lower, i: i, tr: "" };
    panel.ctx = ctx;
    updateSaveButton();
    if (prefs.target) translate(lower).then((t) => { if (panel.ctx === ctx) { ctx.tr = t; $("rd-wtr").textContent = t; updateEntry(ctx); } }, () => {});
    define(lower).then((res) => {
      if (panel.ctx !== ctx) return;
      ctx.base = res ? res.base : "";
      renderDefinition($("rd-def"), $("rd-phon"), lower, res);
    });
    panel.showModal();
  }
  function openSentence(i) {
    stop();
    panel.ctx = { i: i };
    panelBody.innerHTML = "<h2>" + esc(C.bkTranslate) + '</h2><blockquote class="rd-quote" lang="en" dir="ltr">' + quote(i) + "</blockquote>"
      + '<p class="rd-trans" id="rd-str"></p>'
      + '<div class="rd-actions">' + (synth ? '<button type="button" class="rd-mini" data-act="this">▶ ' + esc(C.bkThis) + "</button>" : "")
      + '<a href="' + esc(googleUrl(sentences[i])) + '" target="_blank" rel="noopener noreferrer">' + esc(C.bkOpenGoogle) + "</a></div>";
    if (prefs.target) showTranslation($("rd-str"), sentences[i]);
    else $("rd-str").hidden = true;
    panel.showModal();
  }
  panel.addEventListener("click", (ev) => {
    if (ev.target === panel || ev.target.closest("[data-close]")) { panel.close(); return; }
    const act = ev.target.closest("[data-act]");
    if (!act) return;
    const ctx = panel.ctx;
    if (act.dataset.act === "say") sayWord(ctx.word);
    else if (act.dataset.act === "this") speakFrom(ctx.i, false);
    else if (act.dataset.act === "str") showTranslation($("rd-str"), sentences[ctx.i]);
    else if (act.dataset.act === "from") { panel.close(); speakFrom(ctx.i, true); }
    else if (act.dataset.act === "save") toggleSave(ctx);
  });
  [setDlg, wordsDlg].forEach((dlg) => dlg.addEventListener("click", (ev) => {
    if (ev.target === dlg || ev.target.closest("[data-close]")) dlg.close();
  }));

  /* ---------- word list ---------- */
  function isSaved(w) { return words.some((x) => x.w === w); }
  function saveWords() { store(WORDS_KEY, words); $("rd-words-n").textContent = words.length; markSaved(); }
  function updateSaveButton() {
    const b = panelBody.querySelector('[data-act="save"]');
    if (!b) return;
    const on = isSaved(panel.ctx.word);
    b.textContent = (on ? "★ " : "☆ ") + (on ? C.bkSaved : C.bkSave);
    b.setAttribute("aria-pressed", String(on));
  }
  function toggleSave(ctx) {
    if (isSaved(ctx.word)) words = words.filter((x) => x.w !== ctx.word);
    else words.unshift({ w: ctx.word, base: ctx.base || "", tr: ctx.tr, lang: prefs.target, s: sentences[ctx.i], b: D.slug, c: chapter, t: Date.now(), k: false });
    saveWords();
    updateSaveButton();
  }
  function updateEntry(ctx) {
    const x = words.find((y) => y.w === ctx.word);
    if (x && !x.tr) { x.tr = ctx.tr; saveWords(); }
  }
  function markSaved() {
    const set = new Set(words.map((x) => x.w));
    text.querySelectorAll(".w").forEach((w) => w.classList.toggle("saved", set.has(w.textContent.toLowerCase().replace(/’/g, "'"))));
  }
  function renderWords() {
    const q = $("rd-wq").value.trim().toLowerCase();
    const shown = words.filter((x) => !q || x.w.includes(q) || (x.tr || "").toLowerCase().includes(q));
    $("rd-wlist").innerHTML = shown.map((x) =>
      '<li class="' + (x.k ? "known" : "") + '" data-w="' + esc(x.w) + '"><span><b lang="en" dir="ltr">' + esc(x.w) + "</b>" + (x.tr ? " — " + esc(x.tr) : "") + "</span>"
      + '<span><button type="button" data-wact="say" aria-label="' + esc(C.bkListen) + '">🔊</button> <button type="button" data-wact="del">' + esc(C.bkRemove) + "</button></span>"
      + '<small lang="en" dir="ltr">' + esc(x.s.length > 140 ? x.s.slice(0, 139) + "…" : x.s) + "</small>"
      + '<label><input type="checkbox" data-wact="known"' + (x.k ? " checked" : "") + "> " + esc(C.bkKnown) + "</label></li>").join("");
    $("rd-wempty").hidden = shown.length > 0;
  }
  $("rd-words-btn").addEventListener("click", () => { renderWords(); wordsDlg.showModal(); });
  $("rd-wq").addEventListener("input", renderWords);
  $("rd-wlist").addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-wact]"), li = ev.target.closest("li");
    if (!el || !li) return;
    const x = words.find((y) => y.w === li.dataset.w);
    if (!x) return;
    if (el.dataset.wact === "say") sayWord(x.w);
    else if (el.dataset.wact === "del") { words = words.filter((y) => y !== x); saveWords(); renderWords(); }
    else if (el.dataset.wact === "known") { x.k = el.checked; saveWords(); renderWords(); }
  });

  /* ---------- start ---------- */
  applyPrefs();
  $("rd-words-n").textContent = words.length;
  loadChapter(chapterFromHash() || Math.min(Number(load(POS_KEY, {}).ch) || 1, D.chapters.length));
})();
