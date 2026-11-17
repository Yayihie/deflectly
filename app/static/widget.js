/*! Deflectly embeddable support widget (v1)
   Drop this on your SaaS site to let customers ask questions answered by your trained Deflectly model.
   
   Usage:
     <script defer src="https://your-deflectly.app/static/widget.js"
       data-endpoint="https://your-deflectly.app/api/predict"
       data-title="Support"
       data-primary="#0a6e3c">
     </script>
*/
(function () {
  var BLOCKED = false;
  var endpoint = null;
  var title = "Support";
  var primary = "#0a6e3c";

  function readConfig() {
    var scripts = document.getElementsByTagName("script");
    var me = scripts[scripts.length - 1];
    endpoint = me.getAttribute("data-endpoint") || "/api/predict";
    title = me.getAttribute("data-title") || "Support";
    primary = me.getAttribute("data-primary") || primary;
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function injectStyles() {
    var css = [
      ".deflectly-widget{position:fixed;bottom:24px;right:24px;z-index:2147483647;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;}",
      ".deflectly-bubble{width:56px;height:56px;border-radius:28px;background:"+primary+";color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,0.15);font-weight:700;font-size:12px;}",
      ".deflectly-panel{position:fixed;bottom:92px;right:24px;width:340px;height:520px;background:#fff;border:1px solid #e2e2e0;border-radius:10px;box-shadow:0 10px 40px rgba(0,0,0,0.12);display:none;flex-direction:column;overflow:hidden;}",
      ".deflectly-panel.open{display:flex;}",
      ".deflectly-header{background:"+primary+";color:#fff;padding:12px 16px;font-weight:700;font-size:15px;}",
      ".deflectly-chat{flex:1;padding:14px;overflow-y:auto;display:flex;flex-direction:column;gap:10px;background:#fafaf8;}",
      ".deflectly-msg{max-width:78%;padding:8px 12px;border-radius:10px;font-size:14px;line-height:1.45;}",
      ".deflectly-msg.user{align-self:flex-end;background:#e8f5ee;color:#1a1a1a;}",
      ".deflectly-msg.bot{align-self:flex-start;background:#fff;color:#1a1a1a;border:1px solid #e2e2e0;}",
      ".deflectly-msg .meta{font-size:11px;color:#666;margin-top:4px;}",
      ".deflectly-input{border-top:1px solid #e2e2e0;padding:10px 16px;display:flex;gap:8px;}",
      ".deflectly-input input{flex:1;padding:10px 12px;border:1px solid #e2e2e0;border-radius:6px;font-size:14px;outline:none;}",
      ".deflectly-input button{background:"+primary+";color:#fff;border:none;border-radius:6px;padding:0 16px;font-size:14px;font-weight:700;cursor:pointer;}",
      ".deflectly-close{position:absolute;top:8px;right:8px;background:transparent;border:none;color:#fff;opacity:0.8;cursor:pointer;font-size:18px;}",
      ".deflectly-typing{font-size:12px;color:#666;font-style:italic;}",
      ".deflectly-fb{display:flex;gap:6px;margin-top:4px;font-size:11px;}",
      ".deflectly-fb button{background:none;border:1px solid #d0d0d0;border-radius:4px;padding:1px 8px;cursor:pointer;font-size:11px;}",
      ".deflectly-fb button:hover{border-color:#666;}",
      ".deflectly-correct{display:flex;gap:6px;margin-top:4px;width:100%;}",
      ".deflectly-correct input{flex:1;border:1px solid #d0d0d0;border-radius:4px;padding:4px 8px;font-size:12px;}",
      ".deflectly-correct button{background:" + primary + ";color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:12px;}"
    ].join("\n");
    var style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
  }

  function buildUI() {
    var root = document.createElement("div");
    root.className = "deflectly-widget";

    var bubble = document.createElement("div");
    bubble.className = "deflectly-bubble";
    bubble.setAttribute("aria-label", title);
    bubble.innerHTML = "&#x270D;";

    var panel = document.createElement("div");
    panel.className = "deflectly-panel";
    panel.innerHTML =
      '<div class="deflectly-header"><button class="deflectly-close" aria-label="Close">&times;</button>' +
      escapeHtml(title) + '</div>' +
      '<div class="deflectly-chat" id="deflectly-chat"></div>' +
      '<div class="deflectly-input">' +
      '<input type="text" id="deflectly-input" placeholder="Ask a question…" autocomplete="off"/>' +
      '<button id="deflectly-send">Send</button>' +
      '</div>';

    root.appendChild(bubble);
    root.appendChild(panel);
    document.body.appendChild(root);

    var chat = document.getElementById("deflectly-chat");
    var input = document.getElementById("deflectly-input");
    var send = document.getElementById("deflectly-send");
    var open = false;

    bubble.addEventListener("click", function () {
      open = !open;
      panel.classList.toggle("open", open);
      if (open) input.focus();
    });
    panel.querySelector(".deflectly-close").addEventListener("click", function (e) {
      e.stopPropagation();
      open = false;
      panel.classList.remove("open");
    });

    function addMsg(text, who) {
      var div = document.createElement("div");
      div.className = "deflectly-msg " + who;
      div.innerHTML = escapeHtml(text);
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }

    function setTyping(on) {
      var existing = chat.querySelector(".deflectly-typing");
      if (existing) existing.remove();
      if (on) {
        var t = document.createElement("div");
        t.className = "deflectly-msg bot deflectly-typing";
        t.textContent = "Typing…";
        chat.appendChild(t);
        chat.scrollTop = chat.scrollHeight;
      }
    }

    function ask() {
      var q = input.value.trim();
      if (!q || BLOCKED) return;
      input.value = "";
      addMsg(q, "user");
      setTyping(true);
      fetch(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question: q}),
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          setTyping(false);
          if (d.ok) {
            var div = document.createElement("div");
            div.className = "deflectly-msg bot";
            div.innerHTML = escapeHtml(d.answer) +
              '  <span class="meta">confidence ' + Math.round(d.score * 100) + "%</span>";
            attachFeedback(div, q, d.answer);
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
          } else {
            addMsg("Sorry, I couldn't answer that. Try rephrasing or contact support.", "bot");
          }
        })
        .catch(function () {
          setTyping(false);
          addMsg("Something went wrong. Please try again later.", "bot");
        });
    }

    function attachFeedback(div, question, answer) {
      var fb = document.createElement("div");
      fb.className = "deflectly-fb";
      var up = document.createElement("button");
      up.textContent = "👍";
      var down = document.createElement("button");
      down.textContent = "👎";
      up.onclick = function () {
        fb.remove();
        fetch(feedbackUrl(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: question, predicted_answer: answer, verdict: "up" }),
        });
      };
      down.onclick = function () { promptCorrection(div, question); fb.remove(); };
      fb.appendChild(up);
      fb.appendChild(down);
      div.appendChild(fb);
    }

    function feedbackUrl() {
      return endpoint.replace("/api/predict", "/api/feedback");
    }

    function promptCorrection(div, question) {
      var row = document.createElement("div");
      row.className = "deflectly-correct";
      var input = document.createElement("input");
      input.placeholder = "What's the correct answer?";
      var btn = document.createElement("button");
      btn.textContent = "Teach";
      btn.onclick = function () {
        var corrected = input.value.trim();
        if (!corrected) return;
        row.remove();
        fetch(feedbackUrl(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question: question,
            predicted_answer: null,
            corrected_answer: corrected,
            verdict: "down",
          }),
        })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var note = document.createElement("div");
            note.className = "deflectly-msg bot";
            note.innerHTML = d.ok
              ? "Learned — retrain runs on the next sync."
              : "Couldn't save that correction.";
            chat.appendChild(note);
            chat.scrollTop = chat.scrollHeight;
          });
      };
      row.appendChild(input);
      row.appendChild(btn);
      chat.appendChild(row);
      input.focus();
      chat.scrollTop = chat.scrollHeight;
    }

    send.addEventListener("click", ask);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") ask();
    });
  }

  readConfig();
  injectStyles();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildUI);
  } else {
    buildUI();
  }
})();
