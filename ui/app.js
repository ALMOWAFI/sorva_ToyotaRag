const orb = document.getElementById('orb');
const orbLabel = document.getElementById('orbLabel');
const chatArea = document.getElementById('chatArea');
const textInput = document.getElementById('textInput');
const sendBtn = document.getElementById('sendBtn');
const statusDot = document.getElementById('statusDot');

let conversationHistory = [];
let isListening = false;
let isThinking = false;
let recognition = null;

// ── Voice setup ──────────────────────────────────────────────────────────────

function setupVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    orbLabel.textContent = 'Voice not supported';
    orb.style.opacity = '0.4';
    orb.style.cursor = 'default';
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    isListening = true;
    setOrbState('listening');
  };

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    textInput.value = transcript;
    stopListening();
    sendQuestion(transcript);
  };

  recognition.onerror = () => stopListening();
  recognition.onend = () => { if (isListening) stopListening(); };
}

function startListening() {
  if (!recognition || isThinking) return;
  recognition.start();
}

function stopListening() {
  isListening = false;
  if (recognition) recognition.stop();
  if (!isThinking) setOrbState('idle');
}

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
  } else {
    orbLabel.textContent = 'Tap to speak';
    orbLabel.style.color = '#444';
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

// ── Init ──────────────────────────────────────────────────────────────────────

setupVoice();

fetch('/health')
  .then(r => r.json())
  .then(data => {
    statusDot.className = data.faiss_index === 'ready' && data.ollama === 'connected'
      ? 'status-dot ready'
      : 'status-dot error';
  })
  .catch(() => { statusDot.className = 'status-dot error'; });
