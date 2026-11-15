// Library player (scripts/build_library.py): one question at a time, the right answer shown after each pick, the
// score at the end. Reads #lb-data; nothing is sent or stored. Text goes in with textContent only: one source answer
// is literally "<marquee></marquee>".
(function () {
  var data = JSON.parse(document.getElementById("lb-data").textContent), qs = data.questions, c = data.copy;
  var $ = function (id) { return document.getElementById(id); };
  var player = $("lb-player"), done = $("lb-done"), next = $("lb-next"), fb = $("lb-fb"), opts = $("lb-o");
  var i = 0, score = 0;
  function fill(t, a, b) { return t.replace("{0}", a).replace("{1}", b); }
  function show(focus) {
    var q = qs[i];
    $("lb-pos").textContent = fill(c.lbQuestionOf, i + 1, qs.length);
    $("lb-q").textContent = q.q;
    opts.textContent = "";
    fb.textContent = "";
    fb.className = "lb-feedback";
    next.hidden = true;
    q.o.forEach(function (text, k) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      b.addEventListener("click", function () { pick(k); });
      opts.appendChild(b);
    });
    if (focus) $("lb-q").focus();  // a screen reader reads the new question
  }
  function pick(k) {
    var a = qs[i].a, buttons = opts.children, right = k === a;
    for (var n = 0; n < buttons.length; n++) {
      buttons[n].disabled = true;
      if (n === a) buttons[n].className = "lb-right";
      else if (n === k) buttons[n].className = "lb-wrong";
    }
    if (right) score++;
    fb.textContent = right ? c.lbCorrect : c.lbWrong;
    fb.className = "lb-feedback " + (right ? "lb-right" : "lb-wrong");
    next.textContent = i + 1 < qs.length ? c.lbNext : c.lbFinish;
    next.hidden = false;
    next.focus({ preventScroll: true });
  }
  next.addEventListener("click", function () {
    if (++i < qs.length) return show(true);
    player.hidden = true;
    done.hidden = false;
    $("lb-score").textContent = fill(c.lbScore, score, qs.length);
    $("lb-retry").focus();
  });
  $("lb-retry").addEventListener("click", function () {
    i = score = 0;
    done.hidden = true;
    player.hidden = false;
    show(true);
  });
  $("lb-static").hidden = true;
  player.hidden = false;
  show();
})();
