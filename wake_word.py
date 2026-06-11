"""
wake_word.py — Always-on wake word listener

Runs as a background thread. When "Jarvis" is heard:
  1. Records driver speech until silence
  2. Transcribes via faster-whisper (directly, no HTTP)
  3. Gets answer via RAG + LLM (directly, no HTTP)
  4. Speaks via Kokoro + pyaudio (directly, no HTTP)
  5. Broadcasts state to all WebSocket clients so the UI updates live

State machine:
  idle → listening → transcribing → thinking → speaking → idle
"""

import asyncio
import io
import logging
import os
import struct
import threading
import time
import wave

import numpy as np
import pyaudio

logger = logging.getLogger(__name__)

SAMPLE_RATE   = 16000
CHUNK         = 1280    # ~80ms per chunk — required by OpenWakeWord
SILENCE_RMS    = 300     # RMS below this = silence
SILENCE_SEC    = 1.5     # seconds of silence before stopping recording
MAX_RECORD_SEC = 15      # hard cap
WAKE_THRESHOLD = float(os.getenv("WAKE_THRESHOLD", "0.3"))  # lower = more sensitive
DEBUG_SCORES   = os.getenv("WAKE_DEBUG", "true").lower() == "true"


class WakeWordEngine:
    def __init__(self, broadcast_fn, get_whisper_fn, get_kokoro_fn, retrieve_fn, ask_fn):
        self.broadcast    = broadcast_fn
        self.get_whisper  = get_whisper_fn
        self.get_kokoro   = get_kokoro_fn
        self.retrieve     = retrieve_fn
        self.ask          = ask_fn           # async coroutine: ask(question) → answer str
        self._loop        = None
        self._thread      = None
        self._running     = False

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop    = loop
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Wake word engine started. Say 'Hey Jarvis' to activate.")

    def stop(self):
        self._running = False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push(self, state: str, extra: dict | None = None):
        payload = {"state": state}
        if extra:
            payload.update(extra)
        asyncio.run_coroutine_threadsafe(self.broadcast(payload), self._loop)

    def _rms(self, raw: bytes) -> float:
        shorts = struct.unpack(f"{len(raw)//2}h", raw)
        return (sum(s * s for s in shorts) / max(len(shorts), 1)) ** 0.5

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run(self):
        try:
            from openwakeword.model import Model as OWWModel
        except ImportError:
            logger.error("openwakeword not installed. Run: pip install openwakeword")
            return

        pa     = pyaudio.PyAudio()
        stream = pa.open(rate=SAMPLE_RATE, channels=1,
                         format=pyaudio.paInt16, input=True,
                         frames_per_buffer=CHUNK)

        oww = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        logger.info(f"Listening for 'Hey Jarvis'... (threshold={WAKE_THRESHOLD})")

        debug_counter = 0
        while self._running:
            try:
                raw      = stream.read(CHUNK, exception_on_overflow=False)
                audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                preds    = oww.predict(audio_np)
                score    = preds.get("hey_jarvis", 0.0)

                # Print score every ~3 seconds when non-zero so user can tune threshold
                if DEBUG_SCORES and score > 0.05:
                    logger.info(f"Wake word score: {score:.3f} (need >{WAKE_THRESHOLD})")

                if score > WAKE_THRESHOLD:
                    logger.info(f"Wake word! score={score:.2f}")
                    self._handle_activation(stream, pa)
                    oww = OWWModel(wakeword_models=["hey_jarvis"], inference_framework="onnx")
                    logger.info("Listening for 'Hey Jarvis'...")

            except Exception as e:
                logger.warning(f"Wake loop error: {e}")
                time.sleep(0.5)

        stream.stop_stream()
        stream.close()
        pa.terminate()

    # ── Activation ────────────────────────────────────────────────────────────

    def _handle_activation(self, stream, pa):
        # 1. Record until silence
        self._push("listening")
        frames         = []
        silence_chunks = 0
        silence_limit  = int(SILENCE_SEC * SAMPLE_RATE / CHUNK)
        max_chunks     = int(MAX_RECORD_SEC * SAMPLE_RATE / CHUNK)

        for _ in range(max_chunks):
            raw = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(raw)
            if self._rms(raw) < SILENCE_RMS:
                silence_chunks += 1
                if silence_chunks >= silence_limit:
                    break
            else:
                silence_chunks = 0

        if not frames:
            self._push("idle")
            return

        # Save to WAV buffer
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(frames))
        wav_buf.seek(0)

        # 2. Transcribe directly via Whisper
        self._push("transcribing")
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(wav_buf.read())
                tmp_path = tmp.name

            whisper = self.get_whisper()
            segments, _ = whisper.transcribe(tmp_path, language="en", beam_size=1)
            text = " ".join(s.text.strip() for s in segments).strip()
            os.unlink(tmp_path)
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            self._push("idle")
            return

        if not text:
            self._push("idle")
            return

        logger.info(f"Driver said: {text!r}")
        self._push("listening_result", {"text": text})

        # 3. Get answer via LLM directly
        self._push("thinking")
        try:
            future  = asyncio.run_coroutine_threadsafe(self.ask(text), self._loop)
            answer  = future.result(timeout=120)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            self._push("idle")
            return

        logger.info(f"Answer: {answer[:80]}...")
        self._push("answer", {"question": text, "answer": answer})

        # 4. Speak via Kokoro + pyaudio directly
        self._push("speaking")
        try:
            import soundfile as sf

            pipeline    = self.get_kokoro()
            tts_voice   = os.getenv("TTS_VOICE", "af_heart")
            audio_chunks = []
            for _, _, audio in pipeline(answer, voice=tts_voice, speed=1.0):
                if audio is not None:
                    audio_chunks.append(audio)

            if audio_chunks:
                combined = np.concatenate(audio_chunks)
                buf = io.BytesIO()
                sf.write(buf, combined, samplerate=24000, format="WAV")
                buf.seek(0)

                with wave.open(buf, "rb") as wf:
                    out = pa.open(format=pa.get_format_from_width(wf.getsampwidth()),
                                  channels=wf.getnchannels(),
                                  rate=wf.getframerate(),
                                  output=True)
                    chunk = wf.readframes(1024)
                    while chunk:
                        out.write(chunk)
                        chunk = wf.readframes(1024)
                    out.stop_stream()
                    out.close()

        except Exception as e:
            logger.error(f"TTS error: {e}")

        self._push("idle")
