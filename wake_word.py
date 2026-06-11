"""
wake_word.py — Always-on wake word listener

Runs as a background thread. When "Jarvis" is heard:
  1. Records driver speech until silence
  2. Sends audio to /transcribe
  3. Sends text to /ask
  4. Sends answer to /speak
  5. Broadcasts state to all WebSocket clients so the UI updates live

State machine:
  idle → listening → transcribing → thinking → speaking → idle
"""

import asyncio
import io
import logging
import struct
import threading
import time

import httpx
import numpy as np
import pyaudio

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK = 1280          # ~80ms per chunk — required by OpenWakeWord
SILENCE_THRESHOLD = 200   # RMS below this = silence
SILENCE_DURATION = 1.5    # seconds of silence before stopping recording
MAX_RECORD_SECONDS = 15   # hard cap so it never records forever

BASE_URL = "http://localhost:8000"


class WakeWordEngine:
    def __init__(self, broadcast_fn):
        """
        broadcast_fn: async coroutine to push state to all WebSocket clients
                      called as: broadcast_fn({"state": "listening"})
        """
        self.broadcast = broadcast_fn
        self._loop = None
        self._thread = None
        self._running = False

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Wake word engine started. Say 'Jarvis' to activate.")

    def stop(self):
        self._running = False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _push(self, state: str, extra: dict | None = None):
        payload = {"state": state}
        if extra:
            payload.update(extra)
        asyncio.run_coroutine_threadsafe(self.broadcast(payload), self._loop)

    def _rms(self, data: bytes) -> float:
        shorts = struct.unpack(f"{len(data)//2}h", data)
        return (sum(s * s for s in shorts) / len(shorts)) ** 0.5

    def _run(self):
        try:
            from openwakeword.model import Model as OWWModel
        except ImportError:
            logger.error("openwakeword not installed. Run: pip install openwakeword")
            return

        pa = pyaudio.PyAudio()
        stream = pa.open(
            rate=SAMPLE_RATE,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=CHUNK,
        )

        oww = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        logger.info("Listening for 'Jarvis'...")

        while self._running:
            try:
                raw = stream.read(CHUNK, exception_on_overflow=False)
                audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                predictions = oww.predict(audio_np)

                score = predictions.get("hey_jarvis", 0.0)
                if score > 0.5:
                    logger.info(f"Wake word detected (score={score:.2f})")
                    self._handle_activation(stream, pa)
                    # Reset model state after activation
                    oww = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
                    logger.info("Listening for 'Jarvis'...")

            except Exception as e:
                logger.warning(f"Wake word loop error: {e}")
                time.sleep(0.5)

        stream.stop_stream()
        stream.close()
        pa.terminate()

    def _handle_activation(self, stream, pa):
        self._push("listening")
        logger.info("Recording driver speech...")

        frames = []
        silence_chunks = 0
        silence_limit = int(SILENCE_DURATION * SAMPLE_RATE / CHUNK)
        max_chunks = int(MAX_RECORD_SECONDS * SAMPLE_RATE / CHUNK)

        for _ in range(max_chunks):
            raw = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(raw)
            if self._rms(raw) < SILENCE_THRESHOLD:
                silence_chunks += 1
                if silence_chunks >= silence_limit:
                    break
            else:
                silence_chunks = 0

        if not frames:
            self._push("idle")
            return

        # Convert to WAV in memory
        audio_bytes = b"".join(frames)
        wav_buf = io.BytesIO()
        import wave
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_bytes)
        wav_buf.seek(0)

        # Transcribe
        self._push("transcribing")
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    f"{BASE_URL}/transcribe",
                    files={"audio": ("recording.wav", wav_buf, "audio/wav")},
                )
                resp.raise_for_status()
                text = resp.json().get("text", "").strip()
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            self._push("idle")
            return

        if not text:
            self._push("idle")
            return

        logger.info(f"Transcribed: {text!r}")
        self._push("listening_result", {"text": text})

        # Ask
        self._push("thinking")
        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{BASE_URL}/ask",
                    json={"question": text, "conversation_history": []},
                )
                resp.raise_for_status()
                data = resp.json()
                answer = data.get("answer", "")
        except Exception as e:
            logger.error(f"Ask failed: {e}")
            self._push("idle")
            return

        logger.info(f"Answer: {answer[:80]}...")
        self._push("answer", {"question": text, "answer": answer})

        # Speak
        self._push("speaking")
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(f"{BASE_URL}/speak", json={"text": answer})
                resp.raise_for_status()
                audio_data = resp.content

            # Play audio via pyaudio
            import wave
            wav_io = io.BytesIO(audio_data)
            with wave.open(wav_io, "rb") as wf:
                out_stream = pa.open(
                    format=pa.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True,
                )
                data_chunk = wf.readframes(1024)
                while data_chunk:
                    out_stream.write(data_chunk)
                    data_chunk = wf.readframes(1024)
                out_stream.stop_stream()
                out_stream.close()

        except Exception as e:
            logger.error(f"TTS playback failed: {e}")

        self._push("idle")
