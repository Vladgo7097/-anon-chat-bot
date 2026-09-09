(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  function toast(text) {
    var el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  var me = null;              // telegram_id
  var sessionId = null;
  var isInitiator = false;
  var iceServers = [];
  var forceRelay = true;
  var ws = null;
  var pc = null;
  var localStream = null;     // microphone (always) + camera track when enabled
  var cameraTrack = null;
  var blurLoop = null;
  var startedAt = null;
  var timerHandle = null;
  var micOn = true;
  var prefs = loadPrefs();

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem("devicePrefs") || "{}"); }
    catch (e) { return {}; }
  }

  function leave(message) {
    if (message) try { sessionStorage.setItem("callNotice", message); } catch (e) {}
    cleanup();
    location.href = "/app.html";
  }

  function cleanup() {
    if (timerHandle) clearInterval(timerHandle);
    if (blurLoop) cancelAnimationFrame(blurLoop);
    if (pc) { try { pc.close(); } catch (e) {} pc = null; }
    if (localStream) localStream.getTracks().forEach(function (t) { t.stop(); });
    if (cameraTrack) cameraTrack.stop();
  }

  function setQuality(text, accent) {
    var el = $("call-quality");
    el.textContent = text;
    el.style.borderColor = accent ? "var(--accent-dim)" : "";
    el.style.color = accent ? "var(--accent)" : "";
  }

  function startTimer() {
    if (timerHandle) return;
    startedAt = Date.now();
    timerHandle = setInterval(function () {
      var total = Math.floor((Date.now() - startedAt) / 1000);
      $("call-timer").textContent =
        String(Math.floor(total / 60)).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
    }, 1000);
  }

  /* ---------- signalling over the existing session socket ---------- */

  function send(signal) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "signal", signal: signal }));
    }
  }

  function connectSocket() {
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    ws = new WebSocket(proto + location.host + "/ws/session");
    ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === "signal") {
        if (msg.from === me) return;          // our own echo on the shared channel
        handleSignal(msg.signal);
      } else if (msg.type === "call_ended" || msg.type === "call_declined") {
        leave("Звонок завершён");
      } else if (msg.type === "chat_ended") {
        leave("Диалог завершён");
      }
    };
    ws.onopen = function () { if (isInitiator) startNegotiation(); };
    ws.onclose = function () { setTimeout(connectSocket, 1500); };
  }

  /* ---------- WebRTC ---------- */

  async function buildPeer() {
    pc = new RTCPeerConnection({
      iceServers: iceServers,
      // Relay-only: the two people must never learn each other's IP. It costs
      // server bandwidth, which is the deliberate trade for an anonymity product.
      iceTransportPolicy: forceRelay ? "relay" : "all",
    });

    pc.onicecandidate = function (e) {
      if (e.candidate) send({ candidate: e.candidate });
    };

    pc.ontrack = function (e) {
      var stream = e.streams[0];
      var video = $("remote-video");
      video.srcObject = stream;
      if (e.track.kind === "video") {
        video.style.display = "block";
        $("peer-placeholder").style.display = "none";
      }
      watchVoice(stream);
    };

    pc.onconnectionstatechange = function () {
      if (pc.connectionState === "connected") {
        setQuality("СВЯЗЬ ЕСТЬ", false);
        $("peer-state").textContent = "на связи";
        startTimer();
      } else if (pc.connectionState === "failed") {
        setQuality("СВЯЗЬ ПОТЕРЯНА", true);
        $("peer-state").textContent = "соединение потеряно";
      } else if (pc.connectionState === "disconnected") {
        setQuality("ВОССТАНАВЛИВАЕМ…", true);
      }
    };

    var audioPrefs = {
      noiseSuppression: prefs.noiseSuppression !== false,
      echoCancellation: prefs.echoCancellation !== false,
      autoGainControl: prefs.autoGainControl !== false,
    };
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        audio: Object.assign({ deviceId: prefs.micId ? { exact: prefs.micId } : undefined }, audioPrefs),
        video: false,
      });
    } catch (e) {
      // The saved microphone id no longer matches a real device (unplugged,
      // or saved before device ids were reliably captured) -- a call must
      // still connect with whatever default mic is available, not fail outright.
      localStream = await navigator.mediaDevices.getUserMedia({ audio: audioPrefs, video: false });
    }
    localStream.getTracks().forEach(function (t) { pc.addTrack(t, localStream); });
  }

  async function startNegotiation() {
    if (!pc || pc.signalingState !== "stable") return;
    var offer = await pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: true });
    await pc.setLocalDescription(offer);
    send({ sdp: pc.localDescription });
  }

  async function handleSignal(signal) {
    if (!signal || !pc) return;
    if (signal.sdp) {
      await pc.setRemoteDescription(new RTCSessionDescription(signal.sdp));
      if (signal.sdp.type === "offer") {
        var answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        send({ sdp: pc.localDescription });
      }
    } else if (signal.candidate) {
      try { await pc.addIceCandidate(new RTCIceCandidate(signal.candidate)); } catch (e) {}
    }
  }

  /* ---------- voice activity, purely visual ---------- */

  function watchVoice(stream) {
    if (!stream.getAudioTracks().length) return;
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analyser);
    var data = new Uint8Array(analyser.frequencyBinCount);
    var pulses = [$("pulse1"), $("pulse2"), $("pulse3")];
    setInterval(function () {
      analyser.getByteFrequencyData(data);
      var sum = 0;
      for (var i = 0; i < data.length; i++) sum += data[i];
      var speaking = (sum / data.length) > 8;
      pulses.forEach(function (p) { p.classList.toggle("on", speaking); });
      $("peer-state").textContent = speaking ? "говорит" : "на связи";
    }, 220);
  }

  /* ---------- camera ---------- */

  function showCameraModal() { $("cam-modal").hidden = false; }
  function hideCameraModal() { $("cam-modal").hidden = true; }

  async function enableCamera(blurred) {
    hideCameraModal();
    var raw;
    var videoPrefs = { width: { ideal: 640 }, height: { ideal: 480 } };
    try {
      raw = await navigator.mediaDevices.getUserMedia({
        video: Object.assign({ deviceId: prefs.camId ? { exact: prefs.camId } : undefined }, videoPrefs),
      });
    } catch (e) {
      // Same fallback as the microphone: a stale saved camera id must not
      // just silently fail to turn the camera on at all.
      try {
        raw = await navigator.mediaDevices.getUserMedia({ video: videoPrefs });
      } catch (e2) { toast("Не удалось включить камеру"); return; }
    }

    var track = raw.getVideoTracks()[0];
    if (blurred) track = blurTrack(raw);

    cameraTrack = track;
    var sender = pc.getSenders().find(function (s) { return s.track && s.track.kind === "video"; });
    if (sender) await sender.replaceTrack(track);
    else pc.addTrack(track, localStream);

    // Keep relayed video cheap: this box pays for every byte of it.
    var videoSender = pc.getSenders().find(function (s) { return s.track && s.track.kind === "video"; });
    if (videoSender) {
      var params = videoSender.getParameters();
      params.encodings = [{ maxBitrate: 500000, maxFramerate: 24 }];
      try { await videoSender.setParameters(params); } catch (e) {}
    }
    if (!sender) await startNegotiation();

    $("local-video").srcObject = new MediaStream([track]);
    $("self-tile").classList.add("video-on");
    $("btn-cam").classList.remove("off");
    $("call-camhint").textContent = blurred ? "ТВОЯ КАМЕРА ВКЛ · РАЗМЫТИЕ" : "ТВОЯ КАМЕРА ВКЛЮЧЕНА";
  }

  function blurTrack(stream) {
    // Blur the whole frame, not the background: the point is to hide who you
    // are, and a blurred background with a sharp face would do the opposite.
    var video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    video.play();
    var canvas = document.createElement("canvas");
    canvas.width = 640; canvas.height = 480;
    var ctx = canvas.getContext("2d");
    (function draw() {
      blurLoop = requestAnimationFrame(draw);
      if (video.readyState >= 2) {
        ctx.filter = "blur(14px)";
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      }
    })();
    return canvas.captureStream(24).getVideoTracks()[0];
  }

  function disableCamera() {
    if (blurLoop) { cancelAnimationFrame(blurLoop); blurLoop = null; }
    if (cameraTrack) { cameraTrack.stop(); cameraTrack = null; }
    var sender = pc && pc.getSenders().find(function (s) { return s.track && s.track.kind === "video"; });
    if (sender) sender.replaceTrack(null);
    $("self-tile").classList.remove("video-on");
    $("btn-cam").classList.add("off");
    $("call-camhint").textContent = "КАМЕРЫ ВЫКЛЮЧЕНЫ — ВАС НЕ ВИДНО";
  }

  /* ---------- controls ---------- */

  $("btn-mic").addEventListener("click", function () {
    micOn = !micOn;
    if (localStream) localStream.getAudioTracks().forEach(function (t) { t.enabled = micOn; });
    $("btn-mic").classList.toggle("off", !micOn);
    $("self-mic").textContent = micOn ? "МИКРОФОН ВКЛ" : "МИКРОФОН ВЫКЛ";
    $("self-mic").style.color = micOn ? "var(--accent)" : "var(--muted)";
  });

  $("btn-cam").addEventListener("click", function () {
    if (cameraTrack) disableCamera();
    else showCameraModal();
  });

  $("cam-plain").addEventListener("click", function () { enableCamera(false); });
  $("cam-blur").addEventListener("click", function () { enableCamera(true); });
  $("cam-cancel").addEventListener("click", hideCameraModal);

  $("btn-hangup").addEventListener("click", function () {
    fetch("/api/call/hangup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).finally(function () { leave("Звонок завершён"); });
  });

  window.addEventListener("beforeunload", cleanup);

  /* ---------- boot ---------- */

  fetch("/api/call/state").then(function (r) {
    if (!r.ok) { location.href = "/app.html"; throw new Error("no call"); }
    return r.json();
  }).then(async function (state) {
    me = state.telegram_id;
    sessionId = state.session_id;
    isInitiator = state.is_initiator;
    iceServers = state.ice_servers || [];
    forceRelay = state.force_relay !== false;
    $("peer-label").textContent = "СОБЕСЕДНИК";
    await buildPeer();
    if (prefs.cameraOnJoin) showCameraModal();
    connectSocket();
  }).catch(function () {});
})();
