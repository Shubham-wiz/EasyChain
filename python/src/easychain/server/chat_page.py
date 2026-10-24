"""The hosted chat page and the widget that shows it on another site (Phase 5.4,
docs/publish.md).

The page is one small HTML file with no outside scripts, styles or fonts. It talks to
``/api/chat/<link id>`` on its own server, and keeps the conversation's thread for the
browser tab. The widget (``/widget.js``) adds a button to a page that opens the chat page in
a frame; the chat link lists the sites allowed to do that (``frame-ancestors``).
"""

from __future__ import annotations

import html

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chat</title>
<style>
  :root { --bg: #ffffff; --text: #1d2433; --muted: #5c667a; --line: #dfe3ea;
          --me: #2f5bea; --me-text: #ffffff; --them: #f1f3f7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #14171f; --text: #e8ebf2; --muted: #a2abbd; --line: #2b3140;
            --me: #5b80ff; --me-text: #0b0e14; --them: #1f2430; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
         background: var(--bg); color: var(--text); height: 100vh; display: flex;
         flex-direction: column; }
  header { padding: 12px 16px; border-bottom: 1px solid var(--line); font-weight: 600; }
  main { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column;
         gap: 10px; }
  .msg { max-width: 85%; padding: 8px 12px; border-radius: 14px; white-space: pre-wrap;
         overflow-wrap: anywhere; }
  .me { align-self: flex-end; background: var(--me); color: var(--me-text); }
  .them { align-self: flex-start; background: var(--them); }
  .note { align-self: center; color: var(--muted); font-size: 13px; }
  form { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--line); }
  textarea { flex: 1; resize: none; font: inherit; padding: 8px 10px; border-radius: 10px;
             border: 1px solid var(--line); background: var(--bg); color: var(--text);
             min-height: 44px; }
  button { font: inherit; padding: 0 16px; min-height: 44px; border-radius: 10px; border: 0;
           background: var(--me); color: var(--me-text); cursor: pointer; }
  button:disabled { opacity: .6; cursor: default; }
</style>
</head>
<body>
<header id="title">Chat</header>
<main id="log" aria-live="polite" aria-label="Conversation"></main>
<form id="form">
  <label for="text" style="position:absolute;left:-9999px">Your message</label>
  <textarea id="text" rows="1" placeholder="Write a message" required></textarea>
  <button id="send" type="submit">Send</button>
</form>
<script>
(function () {
  var link = "__LINK__";
  var api = "/api/chat/" + encodeURIComponent(link);
  var store = "easychain.chat." + link;
  var log = document.getElementById("log");
  var text = document.getElementById("text");
  var send = document.getElementById("send");
  var thread = null;
  try { thread = sessionStorage.getItem(store); } catch (e) {}
  function add(kind, words) {
    var item = document.createElement("div");
    item.className = "msg " + kind;
    item.textContent = words;
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
  }
  fetch(api).then(function (r) { return r.json(); }).then(function (info) {
    document.getElementById("title").textContent = info.name || "Chat";
    document.title = info.name || "Chat";
    if (info.available === false) add("note", "This chat isn't available yet.");
  }).catch(function () { add("note", "Couldn't reach the chat. Try again later."); });
  text.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); document.getElementById("form").requestSubmit(); }
  });
  document.getElementById("form").addEventListener("submit", function (e) {
    e.preventDefault();
    var words = text.value.trim();
    if (!words) return;
    add("me", words);
    text.value = "";
    send.disabled = true;
    fetch(api, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify({ message: words, thread_id: thread }) })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        var b = res.body || {};
        if (b.thread_id) { thread = b.thread_id; try { sessionStorage.setItem(store, thread); } catch (e) {} }
        if (!res.ok) add("note", b.message || "Something went wrong. Try again.");
        else if (b.reply) add("them", b.reply);
        else if (b.waiting) add("note", "Someone will get back to you here soon.");
        else add("note", b.error || "No answer came back. Try again.");
      })
      .catch(function () { add("note", "Couldn't send that. Check your connection and try again."); })
      .then(function () { send.disabled = false; text.focus(); });
  });
})();
</script>
</body>
</html>
"""

WIDGET = """// Easy Chain chat widget: <script src="https://YOUR-SERVER/widget.js" data-chat="LINK"></script>
(function () {
  var me = document.currentScript;
  if (!me || !me.dataset.chat) return;
  var origin = new URL(me.src).origin;
  var open = false;
  var frame = document.createElement("iframe");
  frame.src = origin + "/chat/" + encodeURIComponent(me.dataset.chat);
  frame.title = me.dataset.title || "Chat";
  frame.style.cssText = "position:fixed;right:20px;bottom:88px;width:min(380px,calc(100vw - 40px));" +
    "height:min(560px,calc(100vh - 120px));border:1px solid #dfe3ea;border-radius:14px;" +
    "box-shadow:0 12px 40px rgba(0,0,0,.18);display:none;z-index:2147483646;background:#fff";
  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", me.dataset.label || "Open the chat");
  button.setAttribute("aria-expanded", "false");
  button.textContent = me.dataset.button || "Chat";
  button.style.cssText = "position:fixed;right:20px;bottom:20px;min-width:56px;height:56px;padding:0 18px;" +
    "border-radius:28px;border:0;background:" + (me.dataset.color || "#2f5bea") + ";color:#fff;" +
    "font:600 15px system-ui,sans-serif;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.2);z-index:2147483647";
  button.addEventListener("click", function () {
    open = !open;
    frame.style.display = open ? "block" : "none";
    button.setAttribute("aria-expanded", String(open));
  });
  document.body.appendChild(frame);
  document.body.appendChild(button);
})();
"""


def page(link_id: str) -> str:
    """The chat page for one chat link."""
    return _PAGE.replace("__LINK__", html.escape(link_id, quote=True))
