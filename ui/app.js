const orb = document.getElementById('orb');
const orbLabel = document.getElementById('orbLabel');
const chatArea = document.getElementById('chatArea');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const statusDot = document.getElementById('statusDot');

let conversationHistory = [];
let isListening = false;
let isThinking = false;
let mediaRecorder = null;
let audioChunks = [];

// ── Orb states ────────────────────────────────────────────────────────────────

function setOrbState(state) {
  orb.className = 'orb';
  if (state === 'listening') {
    orb.classList.add('listening');
    orbLabel.textContent = 'Listening...';
    orbLabel.style.color = '#818cf8';
  } else if (state === 'thinking') {
    orb.classList.add('thinking');
    orbLabel.textContent = 'Thinking...';
    orbLabel.style.color = '#f59e0b';
  } else if (state === 'transcribing') {
    orb.classList.add('thinking');
    orbLabel.textContent = 'Transcribing...';
    orbLabel.style.color = '#f59e0b';
  } else if (state === 'speaking') {
    orb.classList.add('speaking');
    orbLabel.textContent = 'Speaking...';
    orbLabel.style.color = '#10b981';
  } else {
    orbLabel.textContent = 'Tap to speak';
    orbLabel.style.color = '#444';
  }
}

// ── Voice recording ───────────────────────────────────────────────────────────

async function startListening() {
  if (isThinking || isListening) return;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      setOrbState('transcribing');
      const blob = new Blob(audioChunks, { type: 'audio/webm' });
      await transcribeAndSend(blob);
    };

    mediaRecorder.start();
    isListening = true;
    setOrbState('listening');

  } catch (err) {
    console.error('Microphone error:', err);
    appendMessage('assistant', 'Could not access microphone. Please check permissions.');
  }
}

function stopListening() {
  if (!isListening || !mediaRecorder) return;
  isListening = false;
  mediaRecorder.stop();
}

async function transcribeAndSend(blob) {
  const formData = new FormData();
  formData.append('audio', blob, 'recording.webm');

  try {
    const res = await fetch('/transcribe', { method: 'POST', body: formData });
    if (!res.ok) {
      appendMessage('assistant', 'Could not transcribe audio. Try typing instead.');
      setOrbState('idle');
      return;
    }
    const data = await res.json();
    const text = data.text.trim();
    if (text) {
      textInput.value = text;
      await sendQuestion(text);
    } else {
      appendMessage('assistant', "I didn't catch that. Try again.");
      setOrbState('idle');
    }
  } catch (e) {
    appendMessage('assistant', 'Transcription failed. Server may not be running.');
    setOrbState('idle');
  }
}

// ── Chat ──────────────────────────────────────────────────────────────────────

function appendMessage(role, text, isClarifying = false) {
  const div = document.createElement('div');
  div.className = `message ${role}`;
  if (isClarifying) div.classList.add('clarifying');
  div.innerHTML = `<span>${text}</span>`;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
  return div;
}

function showThinking() {
  const div = document.createElement('div');
  div.className = 'message thinking';
  div.id = 'thinkingBubble';
  div.innerHTML = `<div class="thinking-dots"><span></span><span></span><span></span></div>`;
  chatArea.appendChild(div);
  chatArea.scrollTop = chatArea.scrollHeight;
}

function removeThinking() {
  const el = document.getElementById('thinkingBubble');
  if (el) el.remove();
}

// ── API call ──────────────────────────────────────────────────────────────────

async function sendQuestion(question) {
  if (!question.trim() || isThinking) return;

  isThinking = true;
  sendBtn.disabled = true;
  textInput.value = '';
  setOrbState('thinking');
  statusDot.className = 'status-dot loading';

  appendMessage('driver', question);
  conversationHistory.push({ role: 'driver', text: question });

  showThinking();

  try {
    const res = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        conversation_history: conversationHistory.slice(-6),
      }),
    });

    removeThinking();

    if (!res.ok) {
      const err = await res.json();
      appendMessage('assistant', `Error: ${err.detail || 'Something went wrong.'}`);
    } else {
      const data = await res.json();
      appendMessage('assistant', data.answer, data.is_clarifying_question);
      conversationHistory.push({ role: 'assistant', text: data.answer });
      statusDot.className = 'status-dot ready';
      speakResponse(data.answer);
    }
  } catch (e) {
    removeThinking();
    appendMessage('assistant', 'Could not reach the assistant. Make sure the server is running.');
    statusDot.className = 'status-dot error';
  }

  isThinking = false;
  sendBtn.disabled = false;
  setOrbState('idle');
}

// ── TTS playback ──────────────────────────────────────────────────────────────

let currentAudio = null;

async function speakResponse(text) {
  // Stop any currently playing audio
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }

  try {
    setOrbState('speaking');
    const res = await fetch('/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });

    if (!res.ok) return;

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.onended = () => {
      setOrbState('idle');
      URL.revokeObjectURL(url);
      currentAudio = null;
    };
    currentAudio.play();
  } catch (e) {
    setOrbState('idle');
  }
}

// ── Event listeners ───────────────────────────────────────────────────────────

orb.addEventListener('click', () => {
  if (isThinking) return;
  if (isListening) stopListening();
  else startListening();
});

sendBtn.addEventListener('click', () => sendQuestion(textInput.value));

textInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendQuestion(textInput.value);
});

// ── WebSocket — receives hands-free state from wake word engine ───────────────

function connectWebSocket() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);

    switch (data.state) {
      case 'listening':
        setOrbState('listening');
        break;

      case 'transcribing':
        setOrbState('transcribing');
        break;

      case 'listening_result':
        // Show what the driver said
        if (data.text) appendMessage('driver', data.text);
        break;

      case 'thinking':
        setOrbState('thinking');
        showThinking();
        break;

      case 'answer':
        removeThinking();
        if (data.answer) {
          appendMessage('assistant', data.answer);
          conversationHistory.push({ role: 'driver', text: data.question || '' });
          conversationHistory.push({ role: 'assistant', text: data.answer });
        }
        break;

      case 'speaking':
        setOrbState('speaking');
        statusDot.className = 'status-dot ready';
        break;

      case 'idle':
        setOrbState('idle');
        isThinking = false;
        sendBtn.disabled = false;
        break;
    }
  };

  ws.onclose = () => setTimeout(connectWebSocket, 2000); // auto-reconnect
  ws.onerror = () => ws.close();
}

// ── Init ──────────────────────────────────────────────────────────────────────

fetch('/health')
  .then(r => r.json())
  .then(data => {
    statusDot.className = data.faiss_index === 'ready' && data.ollama === 'connected'
      ? 'status-dot ready'
      : 'status-dot error';
  })
  .catch(() => { statusDot.className = 'status-dot error'; });

connectWebSocket();
