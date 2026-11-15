// Library (scripts/build_library.py), the miniapp's Library on the web (quizjet-bot wwwroot/miniapp/kutuphane.html,
// oyna.html). Two pages use it:
// - /library/: the shelves; the URL hash opens one shelf (#history) with its level and topic filters, and the quiz
//   language picker (when packs exist in several languages) keeps one language on screen.
// - a pack page: the player. 30 s per question; a timed-out question passes unanswered; after a pick the right answer
//   shows and the next question follows by itself (1.2 s when right, 2.2 s when wrong); Previous reviews earlier
//   questions, Next skips (unanswered) or moves on; the result lists every question. Nothing is sent or stored.
// Text goes in with textContent only: one source answer is literally "<marquee></marquee>".
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var each = function (list, fn) { [].forEach.call(list, fn); };
  function fill(t, a, b) { return t.replace("{0}", a).replace("{1}", b); }

  function shelves() {
    var grid = $("lb-shelves"), pick = $("lb-lang"), sections = document.querySelectorAll(".lb-shelf"), filters = {};
    if (pick) {
      try { var saved = localStorage.getItem("lb-lang"); if (saved && pick.querySelector('option[value="' + saved + '"]')) pick.value = saved; } catch (x) {}
      pick.addEventListener("change", function () {
        try { localStorage.setItem("lb-lang", pick.value); } catch (x) {}
        apply();
      });
      $("lb-langs").hidden = false;
    }
    function apply() {
      var open = decodeURIComponent(location.hash.slice(1)), found = false;
      each(sections, function (s) {
        var f = filters[s.id] || (filters[s.id] = { level: "", topic: "" }), n = 0, shown = 0;
        each(s.querySelectorAll(".quiz-list li"), function (li) {
          var mine = !pick || li.getAttribute("data-lang") === pick.value;
          var hit = mine && (!f.level || li.getAttribute("data-level") === f.level) && (!f.topic || li.getAttribute("data-topic") === f.topic);
          n += mine;
          shown += hit;
          li.hidden = !hit;
        });
        s.querySelector(".discover-empty").hidden = shown > 0;
        s.querySelector(".lb-up").hidden = false;
        var card = grid.querySelector('[data-shelf="' + s.id + '"]');
        card.querySelector("small span").textContent = n;
        card.hidden = !n;
        s.hidden = s.id !== open;
        found = found || s.id === open;
      });
      grid.hidden = found;
    }
    each(sections, function (s) {
      each(s.querySelectorAll("[data-f] button"), function (b) {
        b.addEventListener("click", function () {
          var row = b.parentNode;
          filters[s.id][row.getAttribute("data-f")] = b.getAttribute("data-v");
          each(row.children, function (x) { x.setAttribute("aria-pressed", String(x === b)); });
          apply();
        });
      });
    });
    window.addEventListener("hashchange", function () { apply(); window.scrollTo(0, 0); });
    apply();
  }

  function player() {
    var D = JSON.parse($("lb-data").textContent), qs = D.questions, c = D.copy, LIMIT = D.seconds * 1000;
    var home = $("lb-home"), play = $("lb-player"), done = $("lb-done"), opts = $("lb-o"), fb = $("lb-fb");
    var prev = $("lb-prev"), next = $("lb-next");
    var log, askedAt, view, timer, auto, started;   // log[i] = {s: picked option or null, to: timed out, sk: skipped}

    function stop() { clearInterval(timer); clearTimeout(auto); timer = auto = null; }
    function verdict(i) {
      var r = log[i], right = r.s === qs[i].a;
      fb.textContent = r.sk ? c.lbSkipped : r.to ? c.lbTimedOut : right ? c.lbCorrect : c.lbWrong;
      fb.className = "lb-feedback " + (right ? "lb-right" : "lb-wrong");
    }
    // Question i: live (not answered yet, its clock runs from the first time it was shown) or answered (marked).
    function draw(i, reviewing) {
      stop();
      view = i;
      var q = qs[i], live = i === log.length;
      $("lb-pos").textContent = fill(c.lbQuestionOf, i + 1, qs.length);
      $("lb-rv").hidden = !reviewing;
      $("lb-q").textContent = q.q;
      opts.textContent = "";
      q.o.forEach(function (text, k) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = text;
        if (live) b.addEventListener("click", function () { answer(k, ""); });
        else {
          b.disabled = true;
          if (k === q.a) b.className = "lb-right";
          else if (k === log[i].s) b.className = "lb-wrong";
        }
        opts.appendChild(b);
      });
      $("lb-tm").hidden = $("lb-tmt").hidden = !live;
      $("lb-tmt").textContent = "";
      if (live) { fb.textContent = ""; fb.className = "lb-feedback"; } else verdict(i);
      prev.disabled = i === 0;
      next.textContent = (!live && i === qs.length - 1 ? c.lbFinish : c.lbNext) + " ›";
      if (live) {
        if (askedAt[i] === undefined) askedAt[i] = Date.now();
        tick();
        timer = setInterval(tick, 250);
      }
    }
    function tick() {
      var left = LIMIT - (Date.now() - askedAt[view]);
      if (left <= 0) return answer(null, "to");
      $("lb-tmb").style.width = (left / LIMIT * 100).toFixed(1) + "%";
      $("lb-tmt").textContent = fill(c.lbTimeLeft, Math.ceil(left / 1000));
    }
    function answer(k, how) {
      log.push({ s: k, to: how === "to", sk: how === "sk" });
      draw(view, false);
      auto = setTimeout(forward, k === qs[view].a ? 1200 : 2200);
    }
    // Next: skip a live question; from an answered one, the next answered one (reviewing), the live one, or the result.
    function forward() {
      if (view === log.length) return answer(null, "sk");
      if (view + 1 < log.length) return draw(view + 1, true);
      if (log.length < qs.length) { draw(log.length, false); return $("lb-q").focus(); }
      result();
    }
    function result() {
      stop();
      var right = 0, rows = $("lb-rows");
      rows.textContent = "";
      log.forEach(function (r, i) {
        var ok = r.s === qs[i].a, li = document.createElement("li");
        right += ok;
        li.textContent = (ok ? "✅ " : r.sk ? "⏭ " : r.to ? "⏱ " : "❌ ") + qs[i].q;
        rows.appendChild(li);
      });
      play.hidden = true;
      done.hidden = false;
      $("lb-score").textContent = fill(c.lbScore, right, qs.length);
      $("lb-time").textContent = fill(c.lbResultTime, Math.round((Date.now() - started) / 1000));
      $("lb-retry").focus();
    }
    function start() {
      log = [];
      askedAt = [];
      started = Date.now();
      home.hidden = done.hidden = true;
      play.hidden = false;
      draw(0, false);
      $("lb-q").focus();
    }
    prev.addEventListener("click", function () { if (view > 0) draw(view - 1, true); });
    next.addEventListener("click", forward);
    $("lb-start").addEventListener("click", start);
    $("lb-retry").addEventListener("click", start);
    $("lb-back").addEventListener("click", function () {
      done.hidden = true;
      home.hidden = false;
      $("lb-start").focus();
    });
    $("lb-static").hidden = true;
    $("lb-start").hidden = false;
  }

  if ($("lb-shelves")) shelves();
  if ($("lb-data")) player();
})();
