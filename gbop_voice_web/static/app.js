
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
let voiceTurn = 0;
let connectionGeneration = 0;
let outputScope = null;
let outputSequence = 0;
let remotePlaybackReady = false;
let completedOutputId = null;
let turnReplyToResponseId = null;
let completedOutputReceipt = null;
let turnReplyReceipt = null;
let continuityTurnReady = null;
let memberInputCheckpoint = null;
let lastInputCheckpoint = null;
let lastCheckpointInput = "";

function blockMemberContinuity(scope, message = "Context could not be saved safely. Reconnect before continuing.") {
  if (sessionId === scope.session && voiceTurn === scope.turn
      && connectionGeneration === scope.connection) {
    // Rejected speech must not be replayed to the backend after reconnect.
    // Already rendered local transcript lines stay readable, but are not reused.
    timeline = [];
    currentInput = currentOutput = "";
    cleanup(message);
    setState(message, "error");
  }
  return false;
}

function checkpointMemberInput() {
  // The existing provider transcript is already present locally. Retain just
  // one bounded current utterance for privacy intent detection, with only a
  // 600-character server excerpt persisted. Never send the whole timeline.
  const text = currentInput.trim();
  if (!text || !sessionId) return memberInputCheckpoint;
  const scope = { session: sessionId, turn: voiceTurn, connection: connectionGeneration };
  if (text.length > 12000) {
    blockMemberContinuity(scope, "That turn was too long to save safely. Reconnect and use a shorter turn.");
    return Promise.resolve(false);
  }
  lastCheckpointInput = text;
  const key = JSON.stringify([scope.session, scope.turn, text]);
  if (lastInputCheckpoint === key) return memberInputCheckpoint;
  lastInputCheckpoint = key;
  const previous = memberInputCheckpoint;
  const turnReady = continuityTurnReady;
  memberInputCheckpoint = Promise.all([previous, turnReady]).then(async ([prior, ready]) => {
    if (sessionId !== scope.session || voiceTurn !== scope.turn
        || connectionGeneration !== scope.connection) return false;
    if (prior === false || ready === false) return blockMemberContinuity(scope);
    const response = await authFetch("/api/live/context/input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: scope.session, turn_id: scope.turn, text }),
    });
    const accepted = response.ok && (await response.json()).ok === true;
    return accepted || blockMemberContinuity(scope);
  }).catch(() => blockMemberContinuity(scope));
  return memberInputCheckpoint;
}

function acknowledgeMarketResponse(text) {
  const scope = outputScope;
  outputScope = null;
  if (!scope || scope.session !== sessionId || scope.turn !== voiceTurn
      || scope.connection !== connectionGeneration || !text.trim()
      || !remotePlaybackReady || els.remoteAudio.paused || els.remoteAudio.muted
      || els.remoteAudio.ended || els.remoteAudio.volume === 0
      || !(els.remoteAudio.currentTime > scope.audioStartedAt)) return;
  completedOutputId = scope.response;
  const sendReceipt = () => authFetch("/api/live/context/delivered", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: scope.session, turn_id: scope.turn,
      response_id: scope.response, text }),
  }).then(async (response) => response.ok && (await response.json()).ok === true)
    .catch(() => false);
  // Apply an actual spoken opt-out before storing any delivered-answer excerpt.
  const checkpoint = checkpointMemberInput();
  const delivery = checkpoint ? checkpoint.then(ok => ok ? sendReceipt() : false) : sendReceipt();
  // Playback may finish just before the member says Yes. Do not let either
  // continuation request overtake its exact delivery receipt at the server.
  let timeout;
  completedOutputReceipt = Promise.race([delivery,
    new Promise((resolve) => { timeout = setTimeout(() => resolve(false), 5000); }),
  ]).finally(() => clearTimeout(timeout));
}

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

function invalidateMarketContext(closed = false, continuation = false, previousInput = null) {
  voiceTurn += 1;
  turnReplyToResponseId = continuation && !closed ? completedOutputId : null;
  turnReplyReceipt = turnReplyToResponseId ? completedOutputReceipt : null;
  completedOutputId = completedOutputReceipt = null;
  if (!sessionId) return;
  const requestSession = sessionId;
  const requestTurn = voiceTurn;
  const requestConnection = connectionGeneration;
  const reply = turnReplyToResponseId;
  const sendFence = (delivered) => authFetch("/api/live/context/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: requestSession, turn_id: requestTurn, closed,
      previous_input: closed ? null : previousInput,
      continuation: Boolean(delivered && reply), reply_to_response_id: delivered ? reply : null }),
  }).then(async response => response.ok && (await response.json()).ok !== false)
    .catch(() => false);
  if (!turnReplyReceipt) return sendFence(false); // Interruptions/close fence immediately.
  return turnReplyReceipt.then((delivered) => {
    if (sessionId !== requestSession || voiceTurn !== requestTurn
        || connectionGeneration !== requestConnection) return;
    return sendFence(delivered);
  });
}

async function startVoice() {
  if (connected) return;
  const generation = ++connectionGeneration;
  const currentConnection = () => generation === connectionGeneration;

  if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    setState("HTTPS required for microphone", "error");
    return;
  }

  setState("Connecting…", "connecting");
  els.orb.disabled = true;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    if (!currentConnection()) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    mic = stream;

    const connection = new RTCPeerConnection();
    pc = connection;

    connection.ontrack = (event) => {
      if (!currentConnection()) return;
      remotePlaybackReady = false;
      els.remoteAudio.srcObject = event.streams[0];
      els.remoteAudio.play().then(() => {
        if (currentConnection()) remotePlaybackReady = true;
      }).catch(() => {
        if (currentConnection()) remotePlaybackReady = false;
      });
    };

    for (const track of mic.getAudioTracks()) {
      connection.addTrack(track, mic);
    }

    const channel = connection.createDataChannel("oai-events");
    dc = channel;

    channel.addEventListener("open", () => {
      if (!currentConnection()) return;
      console.log("GBOP Live data channel opened");
    });

    channel.addEventListener("message", async ({ data }) => {
      if (!currentConnection()) return;
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
        if (currentInput.length > 12000) {
          checkpointMemberInput();
          return;
        }
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
          checkpointMemberInput();
          if (!sessionId) return;
          if (currentInput.trim()) {
            addLine("user", currentInput);
            currentInput = "";
          }
          await handleDelegation(delegation.id);
        }
        return;
      }

      if (event.type === "session.output_audio.started") {
        checkpointMemberInput();
        if (!sessionId) return;
        completedOutputId = completedOutputReceipt = null;
        outputScope = { session: sessionId, turn: voiceTurn,
          connection: connectionGeneration, response: `browser_${++outputSequence}`,
          audioStartedAt: els.remoteAudio.currentTime };
        setState("Speaking", "speaking");
        return;
      }

      if (event.type === "session.output_audio.stopped") {
        if (currentOutput.trim()) {
          acknowledgeMarketResponse(currentOutput);
          if (!sessionId) return;
          if (currentInput.trim()) {
            addLine("user", currentInput);
            currentInput = "";
          }
          addLine("assistant", currentOutput);
          currentOutput = "";
        }
        outputScope = null;
        setState("Listening", "listening");
        return;
      }

      if (event.type === "session.input_audio.speech_started") {
        const previousInput = currentInput.trim() || lastCheckpointInput;
        if (previousInput.length > 12000) {
          blockMemberContinuity({ session: sessionId, turn: voiceTurn, connection: connectionGeneration },
            "That turn was too long to save safely. Reconnect and use a shorter turn.");
          return;
        }
        // Only an ordinary reply after finished playback may answer a prior
        // clarification. Interruptions must invalidate it with the queued work.
        const continuation = completedOutputId !== null && outputScope === null && !currentOutput.trim();
        // An interrupted transcript is useful history, not a completed reply.
        outputScope = null;
        if (currentOutput.trim()) addLine("assistant", currentOutput);
        currentOutput = "";
        if (currentInput.trim()) addLine("user", currentInput);
        currentInput = "";
        memberInputCheckpoint = lastInputCheckpoint = null;
        lastCheckpointInput = "";
        continuityTurnReady = invalidateMarketContext(false, continuation, previousInput || null);
        setState("Listening", "listening");
        return;
      }

      if (event.type === "session.input_audio.speech_stopped") {
        checkpointMemberInput();
        if (!sessionId) return;
        setState("Thinking", "thinking");
        return;
      }

      if (event.type === "error") {
        console.error(event);
        setState(event.error?.message || "Live error", "error");
      }
    });

    channel.addEventListener("close", () => {
      if (!currentConnection()) return;
      if (connected) cleanup("Disconnected");
    });

    const offer = await connection.createOffer();
    if (!currentConnection()) return;
    await connection.setLocalDescription(offer);
    await waitForIceGathering(connection);
    if (!currentConnection()) return;

    const response = await authFetch("/api/live/session", {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: connection.localDescription.sdp,
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail);
    }

    const answer = await response.json();
    if (!currentConnection()) return;
    sessionId = answer.session_id;

    await connection.setRemoteDescription({
      type: "answer",
      sdp: answer.sdp,
    });
  } catch (err) {
    if (!currentConnection()) return;
    console.error(err);
    cleanup("Connection failed");
    setState(err.message || "Connection failed", "error");
  } finally {
    if (currentConnection()) els.orb.disabled = false;
  }
}

async function handleDelegation(delegationId) {
  const requestSession = sessionId;
  const requestTurn = voiceTurn;
  const requestConnection = connectionGeneration;
  const reply = turnReplyToResponseId;
  const receipt = turnReplyReceipt;
  const checkpoint = memberInputCheckpoint;
  const turnReady = continuityTurnReady;
  setState("Checking GTOP…", "thinking");

  try {
    const delivered = receipt ? await receipt : false;
    const readiness = await Promise.all([checkpoint, turnReady]);
    if (sessionId !== requestSession || voiceTurn !== requestTurn) return;
    if (readiness.some(result => result === false)) {
      blockMemberContinuity({ session: requestSession, turn: requestTurn, connection: requestConnection });
      return;
    }
    const response = await authFetch("/api/delegate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        delegation_id: delegationId,
        history: timeline,
        session_id: requestSession,
        turn_id: requestTurn,
        continuation: Boolean(delivered && reply),
        reply_to_response_id: delivered ? reply : null,
      }),
    });

    const data = await response.json();
    if (sessionId !== requestSession || voiceTurn !== requestTurn) return;
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
    if (sessionId !== requestSession || voiceTurn !== requestTurn) return;

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
  outputScope = null;
  completedOutputId = turnReplyToResponseId = null;
  completedOutputReceipt = turnReplyReceipt = null;
  continuityTurnReady = memberInputCheckpoint = lastInputCheckpoint = null;
  lastCheckpointInput = "";
  remotePlaybackReady = false;
  connectionGeneration += 1;
  invalidateMarketContext(true);
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
  els.orb.disabled = false;
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
