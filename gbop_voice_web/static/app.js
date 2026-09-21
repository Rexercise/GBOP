
const els = {
  loginCard: document.getElementById("loginCard"),
  voiceCard: document.getElementById("voiceCard"),
  loginButton: document.getElementById("loginButton"),
  logoutButton: document.getElementById("logoutButton"),
  memberName: document.getElementById("memberName"),
  loginError: document.getElementById("loginError"),
  orb: document.getElementById("orb"),
  voiceState: document.getElementById("voiceState"),
  statusPill: document.getElementById("statusPill"),
  muteButton: document.getElementById("muteButton"),
  endButton: document.getElementById("endButton"),
  transcript: document.getElementById("transcript"),
  remoteAudio: document.getElementById("remoteAudio"),
};

let pc = null;
let dc = null;
let mic = null;
let connected = false;
let muted = false;
let currentInput = "";
let currentOutput = "";
let timeline = [];
let sessionId = null;

function setState(text, mode = "idle") {
  els.voiceState.textContent = text;
  els.orb.dataset.mode = mode;

  const labels = {
    idle: "Ready",
    connecting: "Connecting",
    listening: "Listening",
    speaking: "Speaking",
    thinking: "Thinking",
    error: "Error",
  };

  els.statusPill.textContent = labels[mode] || text;
  els.statusPill.className = `pill ${mode}`;
}

function addLine(role, text) {
  const clean = (text || "").trim();
  if (!clean) return;

  const placeholder = els.transcript.querySelector(".placeholder");
  if (placeholder) placeholder.remove();

  const item = document.createElement("div");
  item.className = `line ${role}`;

  const who = document.createElement("span");
  who.className = "who";
  who.textContent = role === "user" ? "You" : "GBOP";

  const body = document.createElement("span");
  body.textContent = clean;

  item.append(who, body);
  els.transcript.appendChild(item);
  els.transcript.scrollTop = els.transcript.scrollHeight;

  timeline.push({ role, text: clean });
  if (timeline.length > 18) timeline = timeline.slice(-18);
}

function sendEvent(event) {
  if (!dc || dc.readyState !== "open") return false;
  dc.send(JSON.stringify(event));
  return true;
}

async function authFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    credentials: "same-origin",
  });
}

async function refreshAuthState() {
  els.loginError.textContent = "";

  try {
    const response = await authFetch("/api/me");
    const data = await response.json();

    if (data.authenticated) {
      els.loginCard.classList.add("hidden");
      els.voiceCard.classList.remove("hidden");
      els.memberName.textContent = data.user.is_owner
        ? `${data.user.name} · Owner`
        : data.user.name;
      setState("Tap to start", "idle");
      return true;
    }

    els.voiceCard.classList.add("hidden");
    els.loginCard.classList.remove("hidden");
    if (data.detail) els.loginError.textContent = data.detail;
    return false;
  } catch (err) {
    els.voiceCard.classList.add("hidden");
    els.loginCard.classList.remove("hidden");
    els.loginError.textContent = "Could not check Discord login.";
    return false;
  }
}

function login() {
  window.location.href = "/auth/discord";
}

async function logout() {
  cleanup("Signed out");
  await authFetch("/auth/logout", { method: "POST" });
  window.location.href = "/";
}

async function waitForIceGathering(pc) {
  if (pc.iceGatheringState === "complete") return;

  await new Promise((resolve) => {
    const check = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(resolve, 1800);
  });
}

async function startVoice() {
  if (connected) return;

  if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    setState("HTTPS required for microphone", "error");
    return;
  }

  setState("Connecting…", "connecting");
  els.orb.disabled = true;

  try {
    mic = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    pc = new RTCPeerConnection();

    pc.ontrack = (event) => {
      els.remoteAudio.srcObject = event.streams[0];
      els.remoteAudio.play().catch(() => {});
    };

    for (const track of mic.getAudioTracks()) {
      pc.addTrack(track, mic);
    }

    dc = pc.createDataChannel("oai-events");

    dc.addEventListener("open", () => {
      console.log("GBOP Live data channel opened");
    });

    dc.addEventListener("message", async ({ data }) => {
      let event;
      try {
        event = JSON.parse(data);
      } catch {
        return;
      }

      if (event.type === "session.started") {
        sessionId = event.session?.id || sessionId;
        connected = true;
        els.endButton.disabled = false;
        els.muteButton.disabled = false;
        setState("Listening", "listening");
        return;
      }

      if (event.type === "session.closed") {
        cleanup("Conversation ended");
        return;
      }

      if (event.type === "session.input_transcript.delta") {
        currentInput += event.delta || "";
        setState("Listening", "listening");
        return;
      }

      if (event.type === "session.output_transcript.delta") {
        currentOutput += event.delta || "";
        setState("Speaking", "speaking");
        return;
      }

      if (event.type === "session.delegation.created") {
        const delegation = event.delegation || {};
        if (delegation.target === "client") {
          if (currentInput.trim()) {
            addLine("user", currentInput);
            currentInput = "";
          }
          await handleDelegation(delegation.id);
        }
        return;
      }

      if (event.type === "session.output_audio.started") {
        setState("Speaking", "speaking");
        return;
      }

      if (event.type === "session.output_audio.stopped") {
        if (currentOutput.trim()) {
          addLine("assistant", currentOutput);
          currentOutput = "";
        }
        setState("Listening", "listening");
        return;
      }

      if (event.type === "session.input_audio.speech_started") {
        setState("Listening", "listening");
        return;
      }

      if (event.type === "session.input_audio.speech_stopped") {
        setState("Thinking", "thinking");
        return;
      }

      if (event.type === "error") {
        console.error(event);
        setState(event.error?.message || "Live error", "error");
      }
    });

    dc.addEventListener("close", () => {
      if (connected) cleanup("Disconnected");
    });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitForIceGathering(pc);

    const response = await authFetch("/api/live/session", {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail);
    }

    const answer = await response.json();
    sessionId = answer.session_id;

    await pc.setRemoteDescription({
      type: "answer",
      sdp: answer.sdp,
    });
  } catch (err) {
    console.error(err);
    cleanup("Connection failed");
    setState(err.message || "Connection failed", "error");
  } finally {
    els.orb.disabled = false;
  }
}

async function handleDelegation(delegationId) {
  setState("Checking GTOP…", "thinking");

  try {
    const response = await authFetch("/api/delegate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        delegation_id: delegationId,
        history: timeline,
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Backend delegation failed");
    }

    sendEvent({
      type: "session.commentary.append",
      event_id: `gbop_result_${Date.now()}`,
      delegation_id: delegationId,
      content: data.result,
    });
  } catch (err) {
    console.error(err);

    sendEvent({
      type: "session.commentary.append",
      event_id: `gbop_error_${Date.now()}`,
      delegation_id: delegationId,
      content: "The GTOP backend hit an error. Say that briefly and ask the member to try again.",
    });
  }
}

function toggleMute() {
  if (!mic) return;
  muted = !muted;

  for (const track of mic.getAudioTracks()) {
    track.enabled = !muted;
  }

  els.muteButton.textContent = muted ? "Unmute" : "Mute";
  setState(muted ? "Muted" : "Listening", muted ? "idle" : "listening");
}

function cleanup(message = "Tap to start") {
  connected = false;

  try {
    if (dc && dc.readyState === "open") {
      dc.send(JSON.stringify({
        type: "session.close",
        event_id: `close_${Date.now()}`,
      }));
    }
  } catch {}

  if (mic) {
    for (const track of mic.getTracks()) track.stop();
  }

  if (pc) pc.close();

  mic = null;
  pc = null;
  dc = null;
  sessionId = null;
  muted = false;

  els.remoteAudio.srcObject = null;
  els.endButton.disabled = true;
  els.muteButton.disabled = true;
  els.muteButton.textContent = "Mute";

  if (currentInput.trim()) addLine("user", currentInput);
  if (currentOutput.trim()) addLine("assistant", currentOutput);
  currentInput = "";
  currentOutput = "";

  setState(message, "idle");
}

els.loginButton.addEventListener("click", login);
els.logoutButton.addEventListener("click", logout);
els.orb.addEventListener("click", startVoice);
els.muteButton.addEventListener("click", toggleMute);
els.endButton.addEventListener("click", () => cleanup("Tap to start"));

refreshAuthState();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(console.warn);
}
