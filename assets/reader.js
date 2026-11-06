/* QuizJet book reader: sentence-synced read-aloud, word/sentence lookup, a personal word list.
   Everything runs in this page; nothing is sent to QuizJet and nothing opens a new tab.
   - Read-aloud: the browser's own speech engine (speechSynthesis), one utterance per sentence, so
     the highlighted sentence is always the one being spoken. Word highlight only when the engine
     reports word boundaries; otherwise sentence-level only (no fake precision).
   - Translation: tried in order, all from this page — Google's free endpoint (with meanings by part
     of speech for single words), Chrome's on-device Translator when its language pack is already
     there, then MyMemory. Each attempt has a time limit (Chrome's availability() was seen never to
     settle). A provider that fails or times out is skipped for the rest of the visit ("Try again"
     re-enables them), so a blocked one costs one request, not one per tap.
   - Dictionary: dictionaryapi.dev (English definitions and IPA, free, no key).
   - Reading position, settings, saved words and recent translations: localStorage on this device. */
(function () {
  "use strict";
  const root = document.getElementById("reader");
  if (!root) return;
  const D = JSON.parse(document.getElementById("rd-data").textContent);
  const C = D.copy;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => "&#" + ch.charCodeAt(0) + ";");
  const text = $("rd-text"), chSel = $("rd-ch"), seek = $("rd-seek"), pos = $("rd-pos"), bar = $("rd-bar");
  const playBtn = $("rd-play"), speedBtn = $("rd-speed"), prevBtn = $("rd-prev"), nextBtn = $("rd-next"), trBtn = $("rd-tr");
  const panel = $("rd-panel"), panelBody = $("rd-panel-body"), setDlg = $("rd-set"), wordsDlg = $("rd-words");
  const quizBox = $("rd-quiz"), tools = root.querySelector(".rd-tools"), pgPrev = $("rd-pg-prev"), pgNext = $("rd-pg-next");

  function load(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch (_) { return fallback; } }
  function store(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode or full: keep going */ } }

  const POS_KEY = "qj.book." + D.slug, PREFS_KEY = "qj.reader", WORDS_KEY = "qj.words", TR_KEY = "qj.tr";
  const RATES = [0.6, 0.8, 1, 1.2, 1.5];
  const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
  // Only what the reader explicitly chose is stored; everything else is a default worked out on each load.
  // The first version stored every default on the first visit, so a visit to the English page froze an
  // empty translation language that the Turkish page then inherited: no translation, no Translate button.
  const chosen = load(PREFS_KEY, {});
  if (chosen.v !== 2) { delete chosen.target; chosen.v = 2; }
  const prefs = Object.assign({ font: 19, spacing: "1.8", theme: dark ? "dark" : "light", view: "book", rate: 1,
    target: defaultTarget(), autoTr: false, tipSeen: false, voice: "" }, chosen);
  function setPref(name, value) { prefs[name] = value; chosen[name] = value; store(PREFS_KEY, chosen); }
  // The page language, or on the English page the phone's own language when the reader offers it.
  function defaultTarget() {
    const offered = [...document.querySelectorAll('#rd-set-form [name="target"] option')].map((o) => o.value).filter(Boolean);
    const wanted = D.lang === "en" ? (navigator.languages || [navigator.language || ""]) : [D.lang];
    for (const tag of wanted) {
      const t = String(tag).toLowerCase(), base = t.split("-")[0];
      const hit = offered.find((c) => c.toLowerCase() === t) || offered.find((c) => c.toLowerCase().split("-")[0] === base);
      if (hit) return hit;
    }
    return "";
  }
  let words = load(WORDS_KEY, []);
  let chapter = 1, sentences = [], spans = [], paraStarts = new Set(), active = -1, playing = false, continuous = false, run = 0, userScrollAt = 0;

  /* ---------- settings ---------- */
  function applyPrefs() {
    root.style.setProperty("--rd-font", prefs.font + "px");
    root.style.setProperty("--rd-lh", prefs.spacing);
    document.documentElement.setAttribute("data-rd-theme", prefs.theme);
    text.classList.toggle("study", prefs.view === "study");
    speedBtn.textContent = prefs.rate + "×";
    $("rd-tip").hidden = prefs.tipSeen;
    const form = $("rd-set-form");
    ["spacing", "theme", "view"].forEach((name) => form.querySelectorAll('[name="' + name + '"]').forEach((r) => { r.checked = r.value === String(prefs[name]); }));
    form.elements.target.value = prefs.target;
    form.elements.autoTr.checked = prefs.autoTr;
    layout(); // font, spacing, view and the tip all change what fits on a page
  }
  $("rd-set-form").addEventListener("change", (ev) => {
    const el = ev.target, oldTarget = prefs.target;
    setPref(el.name, el.type === "checkbox" ? el.checked : el.value);
    applyPrefs();
    if (el.name === "target" || el.name === "autoTr") refreshLines(oldTarget !== prefs.target);
    if (el.name === "voice") { pickVoice(); speakFrom(current(), playing && continuous); } // let them hear it
  });
  $("rd-set-form").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-font]");
    if (b) { setPref("font", Math.min(28, Math.max(14, prefs.font + 2 * Number(b.dataset.font)))); applyPrefs(); }
  });
  $("rd-set-btn").addEventListener("click", () => setDlg.showModal());
  $("rd-voice-btn").addEventListener("click", () => { if (synth) pickVoice(); setDlg.showModal(); }); // the voice list sits at the top

  /* ---------- speech ---------- */
  const synth = window.speechSynthesis;
  // macOS/iOS list joke and robot voices as English; they must never read a book.
  const NOVELTY = /^(albert|bad news|bahh|bells|boing|bubbles|cellos|good news|jester|organ|superstar|trinoids|whisper|wobble|zarvox|fred|junior|kathy|ralph|eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley)\b/i;
  // ponytail: the Web Speech API reports no quality, so this reads the names (Edge "Online (Natural)",
  // Apple "...enhanced/premium..." in voiceURI, Chrome "Google ..."); the reader can override it in settings.
  function score(v) {
    const id = v.name + " " + v.voiceURI;
    return (/natural|neural|premium|enhanced/i.test(id) ? 40 : 0) + (/google/i.test(id) ? 20 : 0) + (/GB/i.test(v.lang) ? 5 : 0)
      + (v.localService ? 2 : 0) - (/compact/i.test(id) ? 10 : 0) - (NOVELTY.test(v.name) || /espeak/i.test(id) ? 50 : 0);
  }
  let voice = null, voices = [], utterance = null; // utterance: Chrome drops onend when the object is garbage-collected
  function pickVoice() {
    voices = synth.getVoices().filter((v) => /^en([-_]|$)/i.test(v.lang)).sort((a, b) => score(b) - score(a));
    voice = voices.find((v) => v.voiceURI === prefs.voice) || voices[0] || null;
    $("rd-set-form").elements.voice.replaceChildren(new Option(C.bkVoiceAuto, ""),
      ...voices.filter((v) => score(v) > -40).map((v) => new Option(v.name + " (" + v.lang + ")", v.voiceURI)));
    $("rd-set-form").elements.voice.value = voice && voice.voiceURI === prefs.voice ? prefs.voice : "";
  }
  if (synth && window.SpeechSynthesisUtterance) {
    pickVoice();
    if (synth.addEventListener) synth.addEventListener("voiceschanged", pickVoice);
    else synth.onvoiceschanged = pickVoice;
  } else {
    $("rd-player").hidden = true;
    $("rd-voice").hidden = true;
    $("rd-voice-btn").hidden = true;
    $("rd-this").disabled = true;
    $("rd-novoice").hidden = false;
  }
  function utter(textToSay) {
    if (!voices.length) pickVoice(); // some phones list no voices at load and never fire voiceschanged
    const u = utterance = new SpeechSynthesisUtterance(textToSay);
    u.lang = voice ? voice.lang : "en-GB";
    if (voice) u.voice = voice;
    u.rate = prefs.rate;
    return u;
  }
  // Chrome's network voices go silent about 15 s into an utterance, so a long sentence reaches them in
  // clause-sized parts (start offsets into the sentence). Local voices get the whole sentence: better intonation.
  function partsOf(s) {
    const starts = [0], max = Math.round(180 * Math.min(prefs.rate, 1));
    if (!voice || voice.localService) return starts;
    for (let from = 0; s.length - from > max;) {
      const win = s.slice(from, from + max);
      let cut = Math.max(win.lastIndexOf("; "), win.lastIndexOf(": "), win.lastIndexOf(", "), win.lastIndexOf("—"));
      if (cut < max / 3) cut = win.lastIndexOf(" ");
      from += cut > 0 ? cut + 1 : max;
      starts.push(from);
    }
    return starts;
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
    const busy = synth.speaking || synth.pending;
    synth.cancel();
    playing = true;
    continuous = all;
    setActive(i, 1);
    updatePlay();
    const s = sentences[i], starts = partsOf(s);
    const say = (k) => {
      const u = utter(s.slice(starts[k], starts[k + 1]));
      u.onboundary = (ev) => { if (mine === run && ev.name !== "sentence") markWord(starts[k] + ev.charIndex); };
      u.onend = () => {
        if (mine !== run) return; // a newer command already took over
        if (k + 1 < starts.length) { say(k + 1); return; }
        markWord(-1);
        if (continuous && i + 1 < sentences.length) {
          // A narrator breathes between paragraphs; back-to-back sentences across one sound rushed.
          if (paraStarts.has(i + 1)) setTimeout(() => { if (mine === run) speakFrom(i + 1, true); }, 450 / prefs.rate);
          else speakFrom(i + 1, true);
          return;
        }
        playing = false;
        updatePlay();
        if (continuous) goPage(pageOf(quizBox)); // chapter done: offer the quiz
      };
      u.onerror = () => { if (mine === run) { playing = false; updatePlay(); } };
      synth.speak(u);
    };
    // Safari can drop an utterance queued in the same tick as cancel(); a short gap avoids it.
    if (busy) setTimeout(() => { if (mine === run) say(0); }, 60);
    else say(0);
  }
  function sayWord(w) { if (!synth) return; stop(); synth.speak(utter(w)); }
  function updatePlay() {
    playBtn.textContent = playing && continuous ? "❚❚" : "▶";
    playBtn.setAttribute("aria-label", playing && continuous ? C.bkPause : C.bkPlay);
    keepAwake();
  }
  // A phone that dims and locks mid-chapter stops the speech; hold the screen on while reading aloud.
  let wake = null, waking = false;
  function keepAwake() {
    const want = playing && continuous;
    if (want && !wake && !waking && navigator.wakeLock && document.visibilityState === "visible") {
      waking = true;
      navigator.wakeLock.request("screen").then((lock) => {
        wake = lock;
        lock.addEventListener("release", () => { if (wake === lock) wake = null; });
        if (!(playing && continuous)) lock.release();
      }, () => {}).finally(() => { waking = false; });
    } else if (!want && wake) wake.release();
  }
  document.addEventListener("visibilitychange", keepAwake);
  let litWord = null;
  function markWord(charIndex) {
    if (litWord) litWord.classList.remove("on");
    litWord = null;
    if (charIndex < 0 || active < 0) return;
    for (const w of spans[active].querySelectorAll(".w")) {
      const o = Number(w.dataset.o);
      if (charIndex >= o && charIndex < o + w.textContent.length) { w.classList.add("on"); litWord = w; break; }
    }
    // A sentence can run over onto the next page: turn when the voice gets there, not at the next sentence.
    if (litWord && playing && Date.now() - userScrollAt > 4000 && pageOf(litWord) !== page) goPage(pageOf(litWord));
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
    if (prefs.autoTr && prefs.target) {
      text.querySelectorAll(".tl.auto").forEach((x) => { if (Number(x.dataset.i) !== i) x.remove(); });
      if (!lineFor(i)) showLine(i, true);
    }
    updateTrButton();
    // follow: 0 = never turn the page (the reader tapped it), 1 = only if the reader isn't paging by hand, 2 = always.
    if (follow === 2 || (follow === 1 && Date.now() - userScrollAt > 4000)) goPage(pageOf(spans[i]));
    else savePos();
  }

  /* ---------- pages ---------- */
  // The chapter is laid out in CSS columns one screen wide inside a box that never scrolls by hand; a page is one
  // column, and turning it is a plain smooth scrollTo sideways. Nothing is measured per page: the browser paginates.
  let page = 0, stride = 1, anchor = 0; // anchor: first sentence of the page, kept across re-layouts (font, rotation)
  function layout() {
    text.style.height = Math.max(220, innerHeight - bar.offsetHeight - (text.getBoundingClientRect().top - tools.getBoundingClientRect().top) - 34) + "px";
    const w = text.getBoundingClientRect().width;
    text.style.columnWidth = w + "px";
    stride = w + parseFloat(getComputedStyle(text).columnGap);
    if (spans.length) goPage(pageOf(spans[anchor] || spans[0]), true);
  }
  function pageCount() { return Math.max(1, Math.round((text.scrollWidth + stride - text.clientWidth) / stride)); }
  function pageOf(el) {
    const r = el.getClientRects()[0];
    return r ? Math.floor((r.left - text.getBoundingClientRect().left + text.scrollLeft + 1) / stride) : page;
  }
  function goPage(n, instant) {
    page = Math.max(0, Math.min(n, pageCount() - 1));
    text.scrollTo({ left: page * stride, behavior: instant ? "auto" : "smooth" });
    const t = tools.getBoundingClientRect().top; // keep the toolbar at the top so the page fills the screen
    if (Math.abs(t - 8) > 2) scrollBy(0, t - 8);
    savePos();
  }
  function turn(d) {
    userScrollAt = Date.now();
    if (page + d >= pageCount()) { if (chapter < D.chapters.length) location.hash = chapter + 1; return; }
    goPage(page + d);
  }
  // The first sentence that starts on this page or later; reopening goes back to the page where it starts.
  function topSentence() {
    for (let i = 0; i < spans.length; i++) if (pageOf(spans[i]) >= page) return i;
    return Math.max(spans.length - 1, 0);
  }
  function savePos() {
    if (!spans.length) return;
    anchor = topSentence();
    store(POS_KEY, { ch: chapter, s: Math.max(active, 0), top: anchor });
    const n = pageCount();
    $("rd-progress").textContent = C.bkChapter + " " + chapter + " / " + D.chapters.length + " · " + C.bkPage + " " + (page + 1) + " / " + n;
    pgPrev.disabled = page === 0;
    pgNext.disabled = page >= n - 1 && chapter >= D.chapters.length;
  }
  pgPrev.addEventListener("click", () => turn(-1));
  pgNext.addEventListener("click", () => turn(1));
  let sx = null, sy = 0, swiped = false;
  text.addEventListener("pointerdown", (ev) => { sx = ev.clientX; sy = ev.clientY; });
  text.addEventListener("pointercancel", () => { sx = null; });
  text.addEventListener("pointerup", (ev) => {
    if (sx === null) return;
    const dx = ev.clientX - sx, dy = ev.clientY - sy;
    sx = null;
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy)) return;
    swiped = true; // the click that follows a mouse drag is not a tap on a sentence
    setTimeout(() => { swiped = false; }, 0);
    turn(dx < 0 ? 1 : -1);
  });
  addEventListener("keydown", (ev) => {
    if (document.querySelector("dialog[open]") || /^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName)) return;
    const d = { ArrowRight: 1, PageDown: 1, ArrowLeft: -1, PageUp: -1 }[ev.key];
    if (d) { ev.preventDefault(); turn(d); }
  });
  let resizeTimer = 0;
  addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(layout, 150); });
  // The web fonts can arrive after the first layout and re-wrap the title, pushing the page under the bottom bar.
  if (document.fonts) document.fonts.ready.then(() => { if (spans.length) layout(); });
  ["wheel", "touchmove", "keydown"].forEach((t) => addEventListener(t, () => { userScrollAt = Date.now(); }, { passive: true }));

  /* ---------- chapter loading ---------- */
  const WORD_RE = /[A-Za-z]+(?:['’][A-Za-z]+)*/g;
  function render(data) {
    const frag = document.createDocumentFragment();
    sentences = [];
    spans = [];
    paraStarts = new Set();
    for (const para of data.p) {
      paraStarts.add(sentences.length);
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
    text.replaceChildren(frag, quizBox); // the quiz is the chapter's last page
    seek.max = Math.max(spans.length - 1, 0);
    markSaved();
  }
  function showQuiz() {
    const key = D.chapterQuizzes[String(chapter)] || D.quiz;
    $("rd-quiz-link").href = "https://t.me/" + D.bot + (key ? "?start=" + encodeURIComponent(key) : "");
    $("rd-next-ch").hidden = chapter >= D.chapters.length;
    $("rd-next-ch").href = "#" + (chapter + 1);
    quizBox.hidden = false;
  }
  function loadChapter(n) {
    stop();
    chapter = n;
    chSel.value = String(n);
    active = -1;
    sentences = [];
    spans = []; // savePos() is a no-op until the new chapter is on screen
    page = 0;
    quizBox.hidden = true;
    text.innerHTML = "<p>" + esc(C.bkLoading) + "</p>";
    fetch("/kitaplar/" + D.slug + "/" + n + ".json")
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((data) => {
        if (n !== chapter) return;
        const saved = load(POS_KEY, {});
        render(data);
        showQuiz();
        anchor = saved.ch === n && spans[saved.top] ? saved.top : 0;
        layout();
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
    if (n && n !== chapter) loadChapter(n);
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
  trBtn.addEventListener("click", () => { setActive(current(), 0); showLine(active, false); });
  playBtn.addEventListener("click", () => {
    if (playing && continuous) { stop(); return; }
    userScrollAt = 0; // pressing play means "turn the pages with the voice", even right after paging by hand
    speakFrom(current(), true);
  });
  speedBtn.addEventListener("click", () => {
    setPref("rate", RATES[(RATES.indexOf(prefs.rate) + 1) % RATES.length]);
    applyPrefs();
    if (playing) speakFrom(active, continuous);
  });
  seek.addEventListener("input", () => setActive(Number(seek.value), 2));
  seek.addEventListener("change", () => { if (playing) speakFrom(Number(seek.value), continuous); });

  text.addEventListener("click", (ev) => {
    if (swiped || retried(ev)) return;
    const s = ev.target.closest(".s");
    if (!s) return;
    const i = Number(s.dataset.i);
    const btn = ev.target.closest(".sb");
    if (btn) { setActive(i, 0); btn.classList.contains("sb-play") ? speakFrom(i, false) : showLine(i, false); return; }
    const w = ev.target.closest(".w");
    if (w) { openWord(w, i); return; } // the word panel never moves the reading position
    setActive(i, 0);
    if (playing) speakFrom(i, continuous);
  });

  /* ---------- translation ---------- */
  const gcode = (code) => code.replace(/-BR$/, ""); // Google and Chrome want "pt"
  const onDevice = {};
  // Each provider returns {text, dict?}, null when it does not apply (no need to give up on it), or throws when it failed.
  const providers = {
    google: async (q, tl) => {
      const r = await fetch("https://translate.googleapis.com/translate_a/single?client=gtx&dt=t&dt=bd&dt=md&dj=1&sl=en&tl="
        + encodeURIComponent(gcode(tl)) + "&hl=" + encodeURIComponent(gcode(D.lang)) + "&q=" + encodeURIComponent(q));
      if (!r.ok) throw new Error("google " + r.status);
      const d = await r.json();
      const out = (d.sentences || []).map((x) => x.trans || "").join("").trim();
      if (!out) throw new Error("google empty");
      const dict = (d.dict || []).map((x) => ({ pos: String(x.pos || ""), terms: (x.terms || []).slice(0, 6).map(String) })).filter((x) => x.terms.length);
      const defs = [];
      for (const x of d.definitions || []) for (const en of (x.entry || []).slice(0, 2)) {
        if (en.gloss) defs.push({ pos: String(x.pos || ""), gloss: String(en.gloss), example: String(en.example || "") });
      }
      return { text: out, dict: dict, defs: defs.slice(0, 5) };
    },
    device: async (q, tl) => {
      if (!self.Translator) return null;
      const opts = { sourceLanguage: "en", targetLanguage: gcode(tl) };
      if ((await Translator.availability(opts)) !== "available") return null; // never start a language-pack download unasked
      onDevice[opts.targetLanguage] = onDevice[opts.targetLanguage] || Translator.create(opts);
      return { text: await (await onDevice[opts.targetLanguage]).translate(q) };
    },
    mymemory: async (q, tl) => {
      if (q.length > 480) return null; // MyMemory takes at most 500 bytes per request
      const r = await fetch("https://api.mymemory.translated.net/get?langpair=en%7C" + encodeURIComponent(tl) + "&q=" + encodeURIComponent(q));
      const d = await r.json();
      const t = d && d.responseData && d.responseData.translatedText;
      if (Number(d.responseStatus) !== 200 || !t || /MYMEMORY WARNING/i.test(t)) throw new Error("mymemory " + d.responseStatus);
      return { text: new DOMParser().parseFromString(t, "text/html").documentElement.textContent };
    },
  };
  const ORDER = ["google", "device", "mymemory"], LIMIT_MS = { google: 8000, device: 3000, mymemory: 8000 };
  const within = (ms, p) => Promise.race([p, new Promise((_, no) => setTimeout(() => no(new Error("timeout")), ms))]);
  const down = new Set(), trCache = new Map(), trStore = load(TR_KEY, {});
  function remember(key, res) {
    trStore[key] = res;
    const keys = Object.keys(trStore);
    keys.slice(0, Math.max(0, keys.length - 300)).forEach((k) => delete trStore[k]); // keep the 300 newest
    store(TR_KEY, trStore);
  }
  function translate(q) {
    const key = prefs.target + "|" + q;
    if (trStore[key]) return Promise.resolve(trStore[key]);
    if (!trCache.has(key)) {
      const p = (async () => {
        const failed = [];
        for (const name of ORDER) {
          if (down.has(name)) continue;
          try {
            const res = await within(LIMIT_MS[name], providers[name](q, prefs.target));
            if (res && res.text) { remember(key, res); return res; }
          } catch (err) { down.add(name); failed.push(name + ": " + (err && err.message)); }
        }
        // The one place a visitor (or support) can see why: the browser console.
        console.warn("QuizJet reader: no translation for " + prefs.target, failed.length ? failed : "all providers skipped this visit");
        throw new Error(failed.join(" · "));
      })();
      p.catch(() => trCache.delete(key)); // a failure is not an answer: the next tap asks again
      trCache.set(key, p);
    }
    return trCache.get(key);
  }
  // Puts the translation of q into el; on failure an inline "Try again" re-enables every provider.
  function fill(el, q, done) {
    el.hidden = false;
    el.textContent = C.bkLoading;
    el.trQuery = [q, done];
    translate(q).then((res) => { el.textContent = res.text; if (done) done(res); }, (err) => {
      el.innerHTML = esc(C.bkTrError) + ' <button type="button" class="rd-mini" data-retry>' + esc(C.bkRetry) + "</button>" + why(err);
    });
  }
  // The reason in small print: on a phone there is no console, so a screenshot has to carry it.
  function why(err) { const m = err && err.message; return m ? '<small class="rd-why" lang="en" dir="ltr">' + esc(m) + "</small>" : ""; }
  function retried(ev) {
    const b = ev.target.closest("[data-retry]");
    if (!b) return false;
    const el = b.parentElement;
    down.clear();
    fill(el, ...el.trQuery);
    return true;
  }
  // Inline translation line right under a sentence (EWA-style), toggled by 文A; "auto" lines follow the highlight.
  function lineFor(i) {
    const n = spans[i] && spans[i].nextElementSibling;
    return n && n.classList.contains("tl") ? n : null;
  }
  function showLine(i, auto) {
    if (!prefs.target) { if (!setDlg.open) setDlg.showModal(); return; } // English page: pick a language first
    const old = lineFor(i);
    if (old) { if (!auto) old.remove(); updateTrButton(); return; }
    const line = document.createElement("span");
    line.className = auto ? "tl auto" : "tl";
    line.dataset.i = i;
    line.lang = prefs.target;
    line.dir = "auto";
    spans[i].after(line);
    fill(line, sentences[i]);
    updateTrButton();
  }
  function refreshLines(all) {
    text.querySelectorAll(all ? ".tl" : ".tl.auto").forEach((x) => x.remove());
    if (prefs.autoTr && prefs.target && active >= 0) showLine(active, true);
    updateTrButton();
  }
  function updateTrButton() { trBtn.setAttribute("aria-pressed", String(active >= 0 && !!lineFor(active))); }

  /* ---------- dictionary ---------- */
  const defCache = new Map();
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
  // Resolves to {base, entries}, or null when the dictionary has no entry; rejects when it could not be asked.
  async function define(w) {
    if (defCache.has(w)) return defCache.get(w);
    for (const cand of baseForms(w)) {
      const r = await within(8000, fetch("https://api.dictionaryapi.dev/api/v2/entries/en/" + encodeURIComponent(cand)))
        .catch((err) => { throw new Error("dictionaryapi.dev: " + (err && err.message)); });
      if (r.ok) { const found = { base: cand, entries: await r.json() }; defCache.set(w, found); return found; }
      if (r.status !== 404) throw new Error("dictionaryapi.dev: HTTP " + r.status); // rate limit or outage: don't burn more requests
    }
    defCache.set(w, null); // "not in the dictionary" is an answer; "offline" is not
    return null;
  }
  // ctx.dict: undefined while loading, null for no entry, {entries} when found, {error} when the dictionary failed;
  // ctx.defs: Google's English definitions, the stand-in when the dictionary has nothing to show.
  function showDefs(ctx) {
    if (panel.ctx !== ctx) return;
    const el = $("rd-def");
    if (ctx.dict && ctx.dict.entries) { renderDefinition(el, $("rd-phon"), ctx.word, ctx.dict); return; }
    if (ctx.defs && ctx.defs.length) {
      el.innerHTML = ctx.defs.map((d) => "<li><b>" + esc(d.pos) + "</b> " + esc(d.gloss) + (d.example ? "<br><em>“" + esc(d.example) + "”</em>" : "") + "</li>").join("");
      return;
    }
    if (ctx.dict === undefined) return;
    el.innerHTML = ctx.dict === null ? "<li>" + esc(C.bkNoDef) + "</li>"
      : "<li>" + esc(C.bkDictError) + ' <button type="button" class="rd-mini" data-act="redef">' + esc(C.bkRetry) + "</button>" + why(ctx.dict.error) + "</li>";
  }
  function lookUp(ctx) {
    ctx.dict = undefined;
    $("rd-def").innerHTML = "<li>" + esc(C.bkLoading) + "</li>";
    define(ctx.word).then((res) => { ctx.dict = res; ctx.base = res ? res.base : ""; showDefs(ctx); },
      (err) => { ctx.dict = { error: err }; showDefs(ctx); });
  }
  function renderDefinition(el, phonEl, w, res) {
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

  /* ---------- word panel ---------- */
  function quote(i, w) {
    const s = sentences[i], o = Number(w.dataset.o), len = w.textContent.length;
    return esc(s.slice(0, o)) + "<mark>" + esc(s.slice(o, o + len)) + "</mark>" + esc(s.slice(o + len));
  }
  function openWord(w, i) {
    stop();
    if (!prefs.tipSeen) { setPref("tipSeen", true); applyPrefs(); }
    const word = w.textContent, lower = word.toLowerCase().replace(/’/g, "'");
    panelBody.innerHTML =
      '<h2 lang="en" dir="ltr">' + esc(word) + "</h2>"
      + '<p class="rd-phon" lang="en" dir="ltr" id="rd-phon"></p>'
      + '<p class="rd-trans" id="rd-wtr" dir="auto" hidden></p>'
      + '<div id="rd-mean"></div>'
      + "<h3>" + esc(C.bkContext) + '</h3><blockquote class="rd-quote" lang="en" dir="ltr">' + quote(i, w) + "</blockquote>"
      + '<p class="tl" id="rd-str" dir="auto" hidden></p>'
      + '<div class="rd-actions">'
      + '<button type="button" class="rd-mini" data-act="say">🔊 ' + esc(C.bkListen) + "</button>"
      + '<button type="button" class="rd-mini" data-act="save" aria-pressed="false"></button>'
      + '<button type="button" class="rd-mini" data-act="str">文A ' + esc(C.bkTranslate) + "</button>"
      + (synth ? '<button type="button" class="rd-mini" data-act="from">▶ ' + esc(C.bkFromHere) + "</button>" : "")
      + "</div>"
      + "<h3>" + esc(C.bkDict) + '</h3><ul class="rd-def" lang="en" dir="ltr" id="rd-def"><li>' + esc(C.bkLoading) + "</li></ul>";
    const ctx = { word: lower, i: i, tr: "" };
    panel.ctx = ctx;
    updateSaveButton();
    if (prefs.target) {
      fill($("rd-wtr"), lower, (res) => {
        if (panel.ctx !== ctx) return;
        ctx.tr = res.text;
        ctx.defs = res.defs;
        showDefs(ctx);
        updateEntry(ctx);
        if (res.dict && res.dict.length) {
          $("rd-mean").innerHTML = "<h3>" + esc(C.bkMeanings) + '</h3><ul class="rd-def" dir="auto">'
            + res.dict.map((x) => "<li><b>" + esc(x.pos) + "</b> " + esc(x.terms.join(", ")) + "</li>").join("") + "</ul>";
        }
      });
    }
    lookUp(ctx);
    panel.showModal();
  }
  panel.addEventListener("click", (ev) => {
    if (ev.target === panel || ev.target.closest("[data-close]")) { panel.close(); return; }
    if (retried(ev)) return;
    const act = ev.target.closest("[data-act]");
    if (!act) return;
    const ctx = panel.ctx;
    if (act.dataset.act === "say") sayWord(ctx.word);
    else if (act.dataset.act === "str") {
      if (prefs.target) fill($("rd-str"), sentences[ctx.i]);
      else { panel.close(); setDlg.showModal(); }
    }
    else if (act.dataset.act === "from") { panel.close(); speakFrom(ctx.i, true); }
    else if (act.dataset.act === "save") toggleSave(ctx);
    else if (act.dataset.act === "redef") lookUp(ctx);
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
