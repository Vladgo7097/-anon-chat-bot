(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var SCREENS = ["panel", "search", "chat", "profile", "settings", "premium", "stats", "help", "devices"];

  var me = null;
  var ws = null;
  var wsRetryMs = 1000;
  var searchMode = "idle";   // idle | active
  var devicePrefs = loadPrefs();
  var micStream = null, camStream = null, audioCtx = null, meterTimer = null;

  /* ---------- helpers ---------- */

  function api(path, options) {
    return fetch(path, options || {}).then(function (r) {
      if (r.status === 401) { location.href = "/"; throw new Error("unauthorised"); }
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) { var e = new Error(body.detail || "error"); e.detail = body.detail; e.status = r.status; throw e; }
        return body;
      });
    });
  }

  function toast(text) {
    var el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  function fmtClock(total) {
    var m = Math.floor(total / 60), s = total % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function fmtDuration(seconds) {
    var h = Math.floor(seconds / 3600), m = Math.round((seconds % 3600) / 60);
    return h > 0 ? h + "ч " + m + "м" : m + "м";
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    return String(d.getDate()).padStart(2, "0") + "." + String(d.getMonth() + 1).padStart(2, "0") + "." + d.getFullYear();
  }

  /* ---------- routing ---------- */

  function go(name) {
    SCREENS.forEach(function (s) {
      var el = $("screen-" + s);
      if (el) el.hidden = s !== name;
    });
    if (name === "profile") loadProfile();
    if (name === "settings") loadSettings();
    if (name === "premium") loadPremium();
    if (name === "stats") loadStats();
    if (name === "panel") loadPanel();
    if (name === "devices") loadDevices();
    if (name !== "devices") stopDevicePreview();
  }

  document.addEventListener("click", function (e) {
    var target = e.target.closest("[data-go]");
    if (target) go(target.getAttribute("data-go"));
  });

  /* ---------- panel ---------- */

  function loadPanel() {
    api("/api/profile").then(function (p) {
      $("panel-anon").textContent = p.anon_id;
      var bits = ["в боте с " + fmtDate(p.created_at)];
      if (p.gender) bits.push(p.gender === "male" ? "парень" : "девушка");
      if (p.age) bits.push(p.age + " лет");
      $("panel-sub").textContent = bits.join(" · ");
      var chip = $("panel-premium");
      chip.hidden = !p.is_premium;
      if (p.is_premium) chip.textContent = "PREMIUM ДО " + fmtDate(p.premium_expires);
    }).catch(function () {});
  }

  /* ---------- profile ---------- */

  function loadProfile() {
    api("/api/profile").then(function (p) {
      $("pf-anon").textContent = p.anon_id;
      $("pf-created").textContent = fmtDate(p.created_at);
      document.querySelectorAll("[data-gender]").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-gender") === p.gender);
      });
      if (p.age) { $("pf-age").value = p.age; $("pf-age-val").textContent = p.age; }
      renderPhotos(p);
    }).catch(function () {});
  }

  function renderPhotos(p) {
    var wrap = $("pf-photos");
    wrap.innerHTML = "";
    for (var i = 0; i < p.photo_limit; i++) {
      var slot = document.createElement("button");
      slot.className = "photo-slot" + (i < p.photo_count ? " filled" : "");
      if (i < p.photo_count) {
        slot.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#f5efe4" stroke-opacity=".35" stroke-width="1.4"><circle cx="12" cy="9" r="3.6"/><path d="M4.5 20c0-3.6 3.4-6.4 7.5-6.4s7.5 2.8 7.5 6.4" stroke-linecap="round"/></svg>';
      } else {
        var locked = i >= p.free_photos && !p.is_premium;
        slot.innerHTML = (locked ? '<span class="lock">PREMIUM</span>' : "") +
          '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 5v14M5 12h14" stroke-linecap="round"/></svg>';
        slot.addEventListener("click", function () { $("pf-photo-input").click(); });
      }
      wrap.appendChild(slot);
    }
  }

  document.querySelectorAll("[data-gender]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      api("/api/profile/field", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field: "gender", value: btn.getAttribute("data-gender") }),
      }).then(function () { toast("Сохранено"); loadProfile(); })
        .catch(function (e) { toast(e.detail === "ACTIVE_CHAT" ? "Нельзя менять во время диалога" : "Не удалось сохранить"); });
    });
  });

  $("pf-age").addEventListener("input", function () { $("pf-age-val").textContent = this.value; });
  $("pf-age").addEventListener("change", function () {
    api("/api/profile/field", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field: "age", value: parseInt(this.value, 10) }),
    }).then(function () { toast("Сохранено"); })
      .catch(function (e) { toast(e.detail === "ACTIVE_CHAT" ? "Нельзя менять во время диалога" : "Не удалось сохранить"); });
  });

  $("pf-photo-input").addEventListener("change", function () {
    var file = this.files[0];
    this.value = "";
    if (!file) return;
    var form = new FormData();
    form.append("file", file);
    toast("Загружаю…");
    api("/api/profile/photo", { method: "POST", body: form })
      .then(function () { toast("Фото добавлено"); loadProfile(); })
      .catch(function (e) {
        toast(e.detail === "PHOTO_LIMIT" ? "Лимит фото — нужен Premium или покупка" : "Не удалось загрузить фото");
      });
  });

  $("pf-delete").addEventListener("click", function () {
    if (!confirm("Удалить профиль? Анкета, фото и история диалогов будут стёрты.")) return;
    api("/api/profile/delete", { method: "POST" }).then(function () { location.href = "/"; })
      .catch(function () { toast("Не удалось удалить профиль"); });
  });

  /* ---------- search settings ---------- */

  function loadSettings() {
    api("/api/settings").then(function (s) {
      document.querySelectorAll("#st-gender button").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-g") === s.gender);
      });
      $("st-min").value = s.age_min; $("st-min-val").textContent = s.age_min;
      $("st-max").value = s.age_max; $("st-max-val").textContent = s.age_max;
      $("st-notice").hidden = s.is_premium;
    }).catch(function () {});
  }

  document.querySelectorAll("#st-gender button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("#st-gender button").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
    });
  });
  $("st-min").addEventListener("input", function () { $("st-min-val").textContent = this.value; });
  $("st-max").addEventListener("input", function () { $("st-max-val").textContent = this.value; });

  $("st-save").addEventListener("click", function () {
    var active = document.querySelector("#st-gender button.active");
    var min = parseInt($("st-min").value, 10), max = parseInt($("st-max").value, 10);
    if (min > max) { toast("Минимальный возраст больше максимального"); return; }
    api("/api/settings/search", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gender: active ? active.getAttribute("data-g") : "a", age_min: min, age_max: max }),
    }).then(function () { toast("Настройки сохранены"); })
      .catch(function (e) {
        if (e.detail === "PREMIUM_REQUIRED") toast("Фильтры доступны с Premium");
        else if (e.detail === "ACTIVE_CHAT") toast("Нельзя менять во время диалога");
        else toast("Не удалось сохранить");
      });
  });

  /* ---------- premium ---------- */

  var PLAN_NAMES = { premium_1m: "1 месяц", premium_3m: "3 месяца", premium_12m: "12 месяцев" };
  var PLAN_MONTHS = { premium_1m: 1, premium_3m: 3, premium_12m: 12 };

  function loadPremium() {
    api("/api/premium").then(function (data) {
      var status = $("pm-status");
      status.hidden = !data.is_premium;
      if (data.is_premium) status.textContent = "АКТИВЕН ДО " + fmtDate(data.premium_expires);
      var plans = data.products.filter(function (p) { return PLAN_NAMES[p.code]; });
      var base = {};
      plans.forEach(function (p) { base[p.code] = p.price; });
      var wrap = $("pm-plans");
      wrap.innerHTML = "";
      plans.forEach(function (p) {
        var months = PLAN_MONTHS[p.code];
        var perMonth = Math.round(p.price / months);
        var saving = base.premium_1m ? Math.round(100 - (perMonth * 100 / base.premium_1m)) : 0;
        var discounted = p.price_final < p.price;
        var el = document.createElement("div");
        el.className = "plan" + (discounted ? " best" : "");
        el.innerHTML =
          (discounted ? '<span class="plan-badge">ПЕРВАЯ ПОДПИСКА −' + data.discount_percent + '%</span>' : "") +
          '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;">' +
            '<span style="font-size:16px;">' + PLAN_NAMES[p.code] + '</span>' +
            '<span><span class="mono" style="font-size:20px;' + (discounted ? "color:var(--accent);" : "") + '">' + p.price_final + '</span>' +
            '<span class="mono" style="font-size:13px;color:var(--muted);">⭐' + (discounted ? " вместо " + p.price : "") + '</span></span>' +
          '</div>' +
          (months > 1 && saving > 0 ? '<div style="font-size:12px;color:var(--muted);">' + perMonth + '⭐ в месяц — выгоднее на ' + saving + '%</div>' : "");
        var buy = document.createElement("button");
        buy.className = "btn " + (discounted ? "btn-primary" : "btn-ghost");
        buy.textContent = "ОФОРМИТЬ";
        buy.addEventListener("click", function () { buyPlan(p.code, buy); });
        el.appendChild(buy);
        wrap.appendChild(el);
      });
    }).catch(function () {});
  }

  function buyPlan(code, button) {
    button.disabled = true;
    api("/api/premium/buy", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code }),
    }).then(function (r) {
      toast(r.code === "INVOICE_ALREADY_SENT"
        ? "Счёт уже отправлен в бот — оплати его там"
        : "Счёт отправлен в Telegram — оплати его в чате с ботом");
    }).catch(function (e) {
      toast(e.detail === "BOT_BLOCKED" ? "Разблокируй бота в Telegram, чтобы получить счёт" : "Не удалось выставить счёт");
    }).finally(function () { button.disabled = false; });
  }

  /* ---------- stats ---------- */

  function loadStats() {
    api("/api/stats").then(function (s) {
      $("sx-chats").textContent = s.chats_count;
      $("sx-time").textContent = fmtDuration(s.total_chat_seconds);
      $("sx-likes").textContent = s.likes;
      $("sx-dislikes").textContent = s.dislikes;
      $("sx-streak").textContent = s.streak;
      $("sx-ref").textContent = s.referrals_count;
      $("sx-ref-sub").textContent = "по твоей ссылке, из них " + s.referrals_activated + " дошли до диалога";
      $("sx-ref-link").textContent = s.referral_link;
      $("sx-ach").innerHTML = s.achievements.length
        ? s.achievements.map(function (a) {
            return '<div class="row"><span class="row-label">' + a.title + '</span><span class="mono" style="font-size:11px;color:var(--muted);">' + fmtDate(a.unlocked_at) + '</span></div>';
          }).join("")
        : "Пока ни одного — они открываются за диалоги, раскрытия и стрики.";
    }).catch(function () {});
  }

  $("sx-copy").addEventListener("click", function () {
    var text = $("sx-ref-link").textContent;
    navigator.clipboard.writeText(text).then(function () { toast("Ссылка скопирована"); },
      function () { toast("Не удалось скопировать"); });
  });

  /* ---------- help ---------- */

  document.querySelectorAll(".faq-q").forEach(function (q) {
    q.addEventListener("click", function () {
      var a = q.nextElementSibling;
      if (a) a.hidden = !a.hidden;
    });
  });

  /* ---------- devices ---------- */

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem("devicePrefs") || "{}"); }
    catch (e) { return {}; }
  }

  function savePrefs() {
    try { localStorage.setItem("devicePrefs", JSON.stringify(devicePrefs)); } catch (e) {}
  }

  function loadDevices() {
    var meter = $("dv-meter");
    if (!meter.children.length) {
      for (var i = 0; i < 16; i++) meter.appendChild(document.createElement("i"));
    }
    document.querySelectorAll("[data-flag]").forEach(function (sw) {
      var flag = sw.getAttribute("data-flag");
      if (devicePrefs[flag] !== undefined) sw.classList.toggle("on", !!devicePrefs[flag]);
      else devicePrefs[flag] = sw.classList.contains("on");
    });
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      toast("Браузер не даёт доступ к устройствам");
      return;
    }
    navigator.mediaDevices.enumerateDevices().then(function (devices) {
      fillSelect($("dv-mic"), devices.filter(function (d) { return d.kind === "audioinput"; }), devicePrefs.micId, "Микрофон");
      fillSelect($("dv-out"), devices.filter(function (d) { return d.kind === "audiooutput"; }), devicePrefs.outId, "Динамик");
      fillSelect($("dv-cam"), devices.filter(function (d) { return d.kind === "videoinput"; }), devicePrefs.camId, "Камера");
    });
  }

  function fillSelect(select, devices, chosen, fallback) {
    select.innerHTML = "";
    if (!devices.length) {
      var none = document.createElement("option");
      none.textContent = "Устройства не найдены — дай доступ в браузере";
      select.appendChild(none);
      return;
    }
    devices.forEach(function (d, i) {
      var opt = document.createElement("option");
      opt.value = d.deviceId;
      opt.textContent = d.label || (fallback + " " + (i + 1));
      if (d.deviceId === chosen) opt.selected = true;
      select.appendChild(opt);
    });
  }

  document.querySelectorAll("[data-flag]").forEach(function (sw) {
    sw.addEventListener("click", function () {
      sw.classList.toggle("on");
      devicePrefs[sw.getAttribute("data-flag")] = sw.classList.contains("on");
    });
  });

  $("dv-mic-test").addEventListener("click", function () {
    navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: $("dv-mic").value ? { exact: $("dv-mic").value } : undefined,
        noiseSuppression: !!devicePrefs.noiseSuppression,
        echoCancellation: !!devicePrefs.echoCancellation,
        autoGainControl: !!devicePrefs.autoGainControl,
      },
    }).then(function (stream) {
      stopMicMeter();
      micStream = stream;
      loadDevices();
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var source = audioCtx.createMediaStreamSource(stream);
      var analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      var data = new Uint8Array(analyser.frequencyBinCount);
      var bars = $("dv-meter").children;
      meterTimer = setInterval(function () {
        analyser.getByteFrequencyData(data);
        var sum = 0;
        for (var i = 0; i < data.length; i++) sum += data[i];
        var level = Math.min(bars.length, Math.round((sum / data.length) / 6));
        for (var b = 0; b < bars.length; b++) bars[b].classList.toggle("on", b < level);
      }, 100);
      toast("Говори — полоска должна двигаться");
    }).catch(function () { toast("Не дали доступ к микрофону"); });
  });

  $("dv-out-test").addEventListener("click", function () {
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    var osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.frequency.value = 440;
    gain.gain.value = 0.08;
    osc.connect(gain); gain.connect(ctx.destination);
    osc.start();
    setTimeout(function () { osc.stop(); ctx.close(); }, 600);
  });

  $("dv-cam-test").addEventListener("click", function () {
    navigator.mediaDevices.getUserMedia({
      video: { deviceId: $("dv-cam").value ? { exact: $("dv-cam").value } : undefined },
    }).then(function (stream) {
      stopCamPreview();
      camStream = stream;
      $("dv-preview").srcObject = stream;
      loadDevices();
    }).catch(function () { toast("Не дали доступ к камере"); });
  });

  $("dv-save").addEventListener("click", function () {
    devicePrefs.micId = $("dv-mic").value;
    devicePrefs.outId = $("dv-out").value;
    devicePrefs.camId = $("dv-cam").value;
    savePrefs();
    toast("Настройки устройств сохранены");
  });

  function stopMicMeter() {
    if (meterTimer) { clearInterval(meterTimer); meterTimer = null; }
    if (audioCtx) { audioCtx.close(); audioCtx = null; }
    if (micStream) { micStream.getTracks().forEach(function (t) { t.stop(); }); micStream = null; }
  }

  function stopCamPreview() {
    if (camStream) { camStream.getTracks().forEach(function (t) { t.stop(); }); camStream = null; }
    $("dv-preview").srcObject = null;
  }

  function stopDevicePreview() { stopMicMeter(); stopCamPreview(); }

  /* ---------- search + chat ---------- */

  function setSearchMode(mode) {
    searchMode = mode;
    var active = mode === "active";
    [$("ring1"), $("ring2"), $("ring3"), $("search-status"), $("search-timer"), $("search-online")]
      .forEach(function (el) { el.hidden = !active; });
    $("start-search-btn").hidden = active;
    $("cancel-search-btn").hidden = !active;
    $("search-back").hidden = active;
  }

  function appendSystemLine(text) {
    var div = document.createElement("div");
    div.className = "system-line";
    div.textContent = text;
    $("thread").appendChild(div);
    $("thread").scrollTop = $("thread").scrollHeight;
  }

  function appendBubble(fromMe, contentType, text) {
    var div = document.createElement("div");
    div.className = fromMe ? "bubble-me" : "bubble-them";
    div.textContent = contentType === "photo" ? "📎 Фото" : (text || "");
    $("thread").appendChild(div);
    $("thread").scrollTop = $("thread").scrollHeight;
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
        setSearchMode("active");
        $("search-timer").textContent = fmtClock(msg.elapsed);
        $("online-count").textContent = msg.online;
        break;
      case "search_cancelled":
      case "search_timeout":
        setSearchMode("idle");
        if (msg.type === "search_timeout") toast("Пока никого нет — попробуй ещё раз");
        break;
      case "search_error":
        setSearchMode("idle");
        toast(msg.code === "BANNED" ? "Ты заблокирован" : "Поиск недоступен");
        break;
      case "matched":
        $("thread").innerHTML = "";
        appendSystemLine("Собеседник найден");
        setSearchMode("idle");
        go("chat");
        break;
      case "chat_ended":
        appendSystemLine(msg.reason === "unreachable" ? "Собеседник стал недоступен" : "Диалог завершён");
        setTimeout(function () { go("panel"); }, 900);
        break;
      case "message":
        if (msg.from === (me && me.telegram_id)) break;
        appendBubble(false, msg.content_type, msg.text);
        break;
      case "call_offer":
        if (msg.from === (me && me.telegram_id)) {
          appendSystemLine("Предложение звонка отправлено — ждём ответа");
        } else {
          showCallOffer(msg.session_id);
        }
        break;
      case "call_accepted":
        location.href = "/call.html";
        break;
      case "call_declined":
        appendSystemLine("Собеседник отклонил звонок");
        break;
      case "call_ended":
        appendSystemLine("Звонок завершён");
        break;
    }
  }

  function showCallOffer(sessionId) {
    var card = document.createElement("div");
    card.className = "section";
    card.style.cssText = "align-self:center;max-width:420px;text-align:center;border-style:dashed;";
    card.innerHTML =
      '<div class="mono" style="font-size:11px;letter-spacing:.1em;color:var(--muted);margin-bottom:6px;">ПРЕДЛОЖЕНИЕ ЗВОНКА</div>' +
      '<div style="font-size:13px;color:var(--muted);line-height:1.55;margin-bottom:14px;">Только голос. Камера выключена, включается вручную и видна собеседнику.</div>';
    var row = document.createElement("div");
    row.style.cssText = "display:flex;gap:10px;justify-content:center;";
    var accept = document.createElement("button");
    accept.className = "btn btn-primary";
    accept.textContent = "ПРИНЯТЬ";
    var decline = document.createElement("button");
    decline.className = "btn btn-ghost";
    decline.textContent = "ОТКЛОНИТЬ";
    accept.addEventListener("click", function () {
      api("/api/call/accept", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }).then(function () { location.href = "/call.html"; })
        .catch(function () { toast("Звонок уже недоступен"); card.remove(); });
    });
    decline.addEventListener("click", function () {
      api("/api/call/decline", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }).catch(function () {}).finally(function () { card.remove(); });
    });
    row.appendChild(accept); row.appendChild(decline);
    card.appendChild(row);
    $("thread").appendChild(card);
    $("thread").scrollTop = $("thread").scrollHeight;
  }

  function sendText() {
    var text = $("text-input").value.trim();
    if (!text) return;
    $("text-input").value = "";
    appendBubble(true, "text", text);
    api("/api/chat/send", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    }).catch(function (e) {
      appendSystemLine(e.detail === "CONTACT_WARNING"
        ? "В сообщении найден контакт — отправь ещё раз, если уверен"
        : "Сообщение не доставлено");
    });
  }

  function startSearch() {
    setSearchMode("active");
    if (ws) ws.send(JSON.stringify({ action: "search" }));
  }

  $("start-search-btn").addEventListener("click", startSearch);
  // The panel's own big button goes to the search screen (data-go) and starts
  // looking straight away -- one tap, not two.
  $("panel-search-btn").addEventListener("click", startSearch);
  $("cancel-search-btn").addEventListener("click", function () {
    if (ws) ws.send(JSON.stringify({ action: "cancel" }));
  });
  $("end-chat-btn").addEventListener("click", function () {
    if (ws) ws.send(JSON.stringify({ action: "end" }));
  });
  $("send-btn").addEventListener("click", sendText);
  $("text-input").addEventListener("keydown", function (e) { if (e.key === "Enter") sendText(); });
  $("photo-btn").addEventListener("click", function () { $("photo-input").click(); });
  $("photo-input").addEventListener("change", function () {
    var file = this.files[0];
    this.value = "";
    if (!file) return;
    var form = new FormData();
    form.append("file", file);
    appendBubble(true, "photo", null);
    api("/api/chat/send-photo", { method: "POST", body: form })
      .catch(function () { appendSystemLine("Фото не доставлено"); });
  });
  $("call-btn").addEventListener("click", function () {
    api("/api/call/offer", { method: "POST" })
      .then(function () { appendSystemLine("Предложение звонка отправлено — ждём ответа"); })
      .catch(function (e) {
        toast(e.detail === "PREMIUM_REQUIRED" ? "Звонки доступны с Premium"
            : e.detail === "CALL_IN_PROGRESS" ? "Звонок уже предложен"
            : "Не удалось предложить звонок");
      });
  });

  $("logout-btn").addEventListener("click", function () {
    fetch("/auth/logout", { method: "POST" }).then(function () { location.href = "/"; });
  });

  /* ---------- boot ---------- */

  api("/api/me").then(function (data) {
    me = data;
    if (!me.onboarding_complete) { $("onboarding-banner").hidden = false; return; }
    $("my-id").textContent = "ТЫ: " + me.anon_id;
    if (me.online !== undefined) $("panel-online").textContent = me.online + " онлайн";
    // Calls are a Premium feature, so the button only exists for subscribers.
    $("call-btn").hidden = !me.is_premium;
    $("app-frame").hidden = false;
    try {
      var notice = sessionStorage.getItem("callNotice");
      if (notice) { toast(notice); sessionStorage.removeItem("callNotice"); }
    } catch (e) {}
    go("panel");
    connectSocket();
  }).catch(function () {});
})();
