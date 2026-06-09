#!/usr/bin/env python3
"""HTTP AI inference server for the smart handle system."""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ai_logic import (
    EVENT_PRIORITY,
    EventReferenceDetector,
    ReferenceSoundDetector,
    WhisperTranscriber,
    YamnetClassifier,
    detect_sound_event,
    detect_specific_sound_event,
    detect_text_event,
    merge_events,
)
from raspberry_main import Event, SAMPLE_RATE


class AiService:
    def __init__(
        self,
        whisper_model: str,
        whisper_device: str,
        whisper_compute_type: str,
        sound_threshold: float,
        speed_reference: Path | None,
        speed_match_threshold: float,
        reference_dir: Path | None,
        reference_match_threshold: float,
        speech_threshold: float,
        speech_window_seconds: float,
        scream_threshold: float,
        sound_window_seconds: float,
        hazard_hold_seconds: float,
        voice_rms_threshold: float,
    ) -> None:
        self.transcriber = WhisperTranscriber(
            whisper_model,
            whisper_device,
            whisper_compute_type,
        )
        self.classifier = YamnetClassifier()
        self.sound_threshold = sound_threshold
        self.speech_threshold = speech_threshold
        self.scream_threshold = scream_threshold
        self.speech_window_samples = int(SAMPLE_RATE * speech_window_seconds)
        self.sound_window_samples = int(SAMPLE_RATE * sound_window_seconds)
        self.hazard_hold_seconds = hazard_hold_seconds
        self.voice_rms_threshold = voice_rms_threshold
        self.held_sound_event = Event.NONE
        self.held_sound_label = ""
        self.held_sound_score = 0.0
        self.held_sound_until = 0.0
        self.speech_buffer = None
        self.sound_buffer = None
        self.speed_detector = (
            ReferenceSoundDetector(speed_reference, speed_match_threshold)
            if speed_reference
            else None
        )
        self.reference_detector = (
            EventReferenceDetector(reference_dir, reference_match_threshold)
            if reference_dir
            else None
        )
        self.whisper_lock = threading.Lock()
        self.yamnet_lock = threading.Lock()
        self.speech_buffer_lock = threading.Lock()
        self.sound_buffer_lock = threading.Lock()
        self.hazard_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=2)

    def append_speech_audio(self, audio):
        import numpy as np

        with self.speech_buffer_lock:
            if self.speech_buffer is None:
                self.speech_buffer = np.asarray(audio, dtype=np.float32)
            else:
                self.speech_buffer = np.concatenate((self.speech_buffer, audio))
            self.speech_buffer = self.speech_buffer[-self.speech_window_samples :]
            return self.speech_buffer.copy()

    def clear_speech_audio(self) -> None:
        with self.speech_buffer_lock:
            self.speech_buffer = None

    def append_sound_audio(self, audio):
        import numpy as np

        with self.sound_buffer_lock:
            if self.sound_buffer is None:
                self.sound_buffer = np.asarray(audio, dtype=np.float32)
            else:
                self.sound_buffer = np.concatenate((self.sound_buffer, audio))
            self.sound_buffer = self.sound_buffer[-self.sound_window_samples :]
            return self.sound_buffer.copy()

    def hold_hazard(
        self, event: Event, label: str, score: float
    ) -> tuple[Event, str, float]:
        now = time.monotonic()
        with self.hazard_lock:
            if event is not Event.NONE:
                if (
                    self.held_sound_event is Event.NONE
                    or event == self.held_sound_event
                    or EVENT_PRIORITY[event] > EVENT_PRIORITY[self.held_sound_event]
                ):
                    self.held_sound_event = event
                    self.held_sound_label = label
                    self.held_sound_score = score
                self.held_sound_until = now + self.hazard_hold_seconds
            if now < self.held_sound_until:
                return (
                    self.held_sound_event,
                    self.held_sound_label,
                    self.held_sound_score,
                )
            self.held_sound_event = Event.NONE
            self.held_sound_label = ""
            self.held_sound_score = 0.0
            return Event.NONE, "", 0.0

    def transcribe(self, audio, should_transcribe: bool) -> tuple[str, Event]:
        if not should_transcribe:
            return "", Event.NONE
        with self.whisper_lock:
            text = self.transcriber.transcribe(audio)
        return text, detect_text_event(text)

    def classify_sound(self, audio) -> tuple[Event, str, float, list[tuple[str, float]]]:
        with self.yamnet_lock:
            predictions = self.classifier.classify(audio)
        sound_event, sound_label, sound_score = detect_sound_event(
            predictions, self.sound_threshold
        )
        scream_event, scream_label, scream_score = detect_specific_sound_event(
            predictions, Event.SCREAM, self.scream_threshold
        )
        if scream_event is Event.SCREAM:
            sound_event, sound_label, sound_score = scream_event, scream_label, scream_score
        return sound_event, sound_label, sound_score, predictions

    def analyze(self, audio) -> dict:
        started_at = time.monotonic()
        speech_audio = self.append_speech_audio(audio)
        sound_audio = self.append_sound_audio(audio)
        sound_future = self.executor.submit(self.classify_sound, sound_audio)
        speed_match, speed_score = (
            self.speed_detector.matches(audio)
            if self.speed_detector
            else (False, 0.0)
        )
        reference_event, reference_name, reference_score = (
            self.reference_detector.detect(audio)
            if self.reference_detector
            else (Event.NONE, "", 0.0)
        )
        sound_event, sound_label, sound_score, predictions = sound_future.result()
        sound_event, sound_label, sound_score = self.hold_hazard(
            sound_event, sound_label, sound_score
        )
        voice_labels = ("speech", "conversation", "narration", "monologue")
        speech_score = max(
            (
                score
                for label, score in predictions
                if any(name in label.lower() for name in voice_labels)
            ),
            default=0.0,
        )
        import numpy as np

        voice_rms = float(np.sqrt(np.mean(np.square(speech_audio))))
        # External hazards should not wait for the slower speech recognizer.
        text, stt_event = self.transcribe(
            speech_audio,
            sound_event is Event.NONE
            and (
                speech_score >= self.speech_threshold
                or voice_rms >= self.voice_rms_threshold
            ),
        )
        if speed_match:
            stt_event = merge_events(stt_event, Event.SPEED_WARNING)
        if stt_event is Event.NONE and reference_event is not Event.NONE:
            stt_event = reference_event
        if stt_event is not Event.NONE:
            self.clear_speech_audio()
        final_event = merge_events(stt_event, sound_event)
        top_predictions = [
            {"label": label, "score": round(score, 4)}
            for label, score in predictions[:5]
        ]
        logging.info(
            "Analysis completed in %.2fs: speech_window=%.2fs sound_window=%.2fs text=%r sound=%s(%.3f) reference=%s(%.3f, %s) final=%s top=%s",
            time.monotonic() - started_at,
            len(speech_audio) / SAMPLE_RATE,
            len(sound_audio) / SAMPLE_RATE,
            text,
            sound_label or "none",
            sound_score,
            reference_event.value,
            reference_score,
            reference_name or "none",
            final_event.value,
            top_predictions,
        )
        return {
            "event": final_event.value,
            "text": text,
            "stt_event": stt_event.value,
            "sound_event": sound_event.value,
            "sound_label": sound_label,
            "sound_score": sound_score,
            "speech_score": speech_score,
            "voice_rms": voice_rms,
            "speed_reference_score": speed_score,
            "reference_event": reference_event.value,
            "reference_name": reference_name,
            "reference_score": reference_score,
            "top_predictions": top_predictions,
        }


def make_handler(service: AiService, api_key: str | None):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self.send_json(200, {"status": "ok"}) if self.path == "/health" else self.send_json(
                404, {"error": "not found"}
            )

        def do_POST(self) -> None:
            if self.path != "/analyze":
                self.send_json(404, {"error": "not found"})
                return
            if api_key and self.headers.get("X-API-Key") != api_key:
                self.send_json(401, {"error": "invalid API key"})
                return
            if self.headers.get("X-Sample-Rate") != str(SAMPLE_RATE):
                self.send_json(400, {"error": f"sample rate must be {SAMPLE_RATE}"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > SAMPLE_RATE * 4 * 30 or length % 4 != 0:
                    raise ValueError("audio payload must be between 0 and 30 seconds")
                import numpy as np

                result = service.analyze(np.frombuffer(self.rfile.read(length), dtype="<f4"))
                self.send_json(200, result)
            except Exception:
                logging.exception("Analysis failed")
                self.send_json(500, {"error": "analysis failed"})

        def log_message(self, message: str, *args) -> None:
            logging.info("%s - %s", self.client_address[0], message % args)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--whisper-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--whisper-compute-type", default="int8")
    parser.add_argument("--sound-threshold", type=float, default=0.10)
    parser.add_argument("--speech-threshold", type=float, default=0.10)
    parser.add_argument("--scream-threshold", type=float, default=0.08)
    parser.add_argument("--speech-window-seconds", type=float, default=2.0)
    parser.add_argument("--sound-window-seconds", type=float, default=1.0)
    parser.add_argument("--hazard-hold-seconds", type=float, default=5.0)
    parser.add_argument("--voice-rms-threshold", type=float, default=0.005)
    parser.add_argument("--speed-reference", type=Path)
    parser.add_argument("--speed-match-threshold", type=float, default=0.55)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--reference-match-threshold", type=float, default=0.55)
    parser.add_argument("--api-key", default=os.environ.get("SMART_HANDLE_API_KEY"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = AiService(
        args.whisper_model,
        args.whisper_device,
        args.whisper_compute_type,
        args.sound_threshold,
        args.speed_reference,
        args.speed_match_threshold,
        args.reference_dir,
        args.reference_match_threshold,
        args.speech_threshold,
        args.speech_window_seconds,
        args.scream_threshold,
        args.sound_window_seconds,
        args.hazard_hold_seconds,
        args.voice_rms_threshold,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service, args.api_key))
    logging.info("AI server listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
