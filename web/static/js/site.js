(function () {
  "use strict";

  var els = {
    onboardingBanner: document.getElementById("onboarding-banner"),
    appFrame: document.getElementById("app-frame"),
    myId: document.getElementById("my-id"),
    logoutBtn: document.getElementById("logout-btn"),

    searchView: document.getElementById("search-view"),
    ring1: document.getElementById("ring1"),
    ring2: document.getElementById("ring2"),
    ring3: document.getElementById("ring3"),
    searchStatus: document.getElementById("search-status"),
    searchTimer: document.getElementById("search-timer"),
    searchOnline: document.getElementById("search-online"),
    onlineCount: document.getElementById("online-count"),
    startBtn: document.getElementById("start-search-btn"),
    cancelBtn: document.getElementById("cancel-search-btn"),

    chatView: document.getElementById("chat-view"),
    thread: document.getElementById("thread"),
    endChatBtn: document.getElementById("end-chat-btn"),
    photoBtn: document.getElementById("photo-btn"),
    photoInput: document.getElementById("photo-input"),
    textInput: document.getElementById("text-input"),
    sendBtn: document.getElementById("send-btn"),
  };

  var myAnonId = null;
  var myTelegramId = null;
  var ws = null;
  var wsRetryMs = 1000;

  function setMode(mode) {
    // mode: "search-idle" | "search-active" | "chat"
    var searching = mode === "search-active";
    [els.ring1, els.ring2, els.ring3, els.searchStatus, els.searchTimer, els.searchOnline].forEach(function (el) {
      el.hidden = !searching;
    });
    els.startBtn.hidden = mode !== "search-idle";
    els.cancelBtn.hidden = !searching;
    els.searchView.hidden = mode === "chat";
    els.chatView.hidden = mode !== "chat";
  }

  function fmtClock(totalSeconds) {
    var m = Math.floor(totalSeconds / 60), s = totalSeconds % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function appendSystemLine(text) {
    var div = document.createElement("div");
    div.className = "system-line";
    div.textContent = text;
    els.thread.appendChild(div);
    els.thread.scrollTop = els.thread.scrollHeight;
  }

  function appendBubble(fromMe, contentType, text) {
    var div = document.createElement("div");
    div.className = fromMe ? "bubble-me" : "bubble-them";
    if (contentType === "photo") {
      div.classList.add("bubble-photo");
      div.textContent = "📎 Фото пришло собеседнику" + (fromMe ? "" : " — открой в Telegram, если фото не подгрузилось.");
    } else {
      div.textContent = text || "";
    }
    els.thread.appendChild(div);
    els.thread.scrollTop = els.thread.scrollHeight;
  }

  function connectSocket() {
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(proto + location.host + "/ws/session");

    ws.onopen = function () { wsRetryMs = 1000; };

    ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      handleServerMessage(msg);
    };

    ws.onclose = function () {
      setTimeout(connectSocket, wsRetryMs);
      wsRetryMs = Math.min(wsRetryMs * 2, 15000);
    };
  }

  function handleServerMessage(msg) {
    switch (msg.type) {
      case "error":
      case "onboarding_required":
        location.href = "/";
        break;
      case "search_status":
        setMode("search-active");
        els.searchTimer.textContent = fmtClock(msg.elapsed);
        els.onlineCount.textContent = msg.online;
        break;
      case "search_cancelled":
        setMode("search-idle");
        break;
      case "search_timeout":
        setMode("search-idle");
        break;
      case "matched":
        els.thread.innerHTML = "";
        appendSystemLine("Собеседник найден");
        setMode("chat");
        break;
      case "chat_ended":
        appendSystemLine(msg.reason === "unreachable" ? "Собеседник стал недоступен" : "Диалог завершён");
        setTimeout(function () { setMode("search-idle"); }, 800);
        break;
      case "message":
        // A message this tab itself just sent is already rendered
        // optimistically by sendText/sendPhoto; publish_delivery still
        // echoes it back on the same channel, so skip our own id here or
        // it would show up twice.
        if (msg.from === myTelegramId) break;
        appendBubble(false, msg.content_type, msg.text);
        break;
    }
  }

  function sendText() {
    var text = els.textInput.value.trim();
    if (!text) return;
    els.textInput.value = "";
    appendBubble(true, "text", text);
    fetch("/api/chat/send", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    }).then(function (r) {
      if (!r.ok) appendSystemLine("Сообщение не доставлено");
    });
  }

  function sendPhoto(file) {
    var form = new FormData();
    form.append("file", file);
    appendBubble(true, "photo", null);
    fetch("/api/chat/send-photo", { method: "POST", body: form }).then(function (r) {
      if (!r.ok) appendSystemLine("Фото не доставлено");
    });
  }

  els.startBtn.addEventListener("click", function () {
    setMode("search-active");
    ws && ws.send(JSON.stringify({ action: "search" }));
  });
  els.cancelBtn.addEventListener("click", function () {
    ws && ws.send(JSON.stringify({ action: "cancel" }));
  });
  els.endChatBtn.addEventListener("click", function () {
    ws && ws.send(JSON.stringify({ action: "end" }));
  });
  els.sendBtn.addEventListener("click", sendText);
  els.textInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") sendText();
  });
  els.photoBtn.addEventListener("click", function () { els.photoInput.click(); });
  els.photoInput.addEventListener("change", function () {
    if (els.photoInput.files[0]) sendPhoto(els.photoInput.files[0]);
    els.photoInput.value = "";
  });
  els.logoutBtn.addEventListener("click", function () {
    fetch("/auth/logout", { method: "POST" }).then(function () { location.href = "/"; });
  });

  fetch("/api/me").then(function (r) {
    if (!r.ok) { location.href = "/"; return null; }
    return r.json();
  }).then(function (me) {
    if (!me) return;
    if (!me.onboarding_complete) {
      els.onboardingBanner.hidden = false;
      return;
    }
    myAnonId = me.anon_id;
    myTelegramId = me.telegram_id;
    els.myId.textContent = "ТЫ: " + me.anon_id;
    els.appFrame.hidden = false;
    setMode("search-idle");
    connectSocket();
  });
})();
