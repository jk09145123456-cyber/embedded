"""AI models and event detection shared by the PC inference server."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from raspberry_main import Event, SAMPLE_RATE


EVENT_PRIORITY = {
    Event.NONE: 0,
    Event.RIGHT_TURN: 1,
    Event.LEFT_TURN: 2,
    Event.SPEED_WARNING: 3,
    Event.HORN: 4,
    Event.SCREAM: 5,
    Event.SIREN: 6,
}

TEXT_KEYWORDS = {
    Event.SPEED_WARNING: (
        "속도위반",
        "속도 위반",
        "과속",
        "제한속도",
        "제한 속도",
        "속도를 줄이세요",
    ),
    Event.LEFT_TURN: ("좌회전", "왼쪽", "좌측"),
    Event.RIGHT_TURN: ("우회전", "오른쪽", "우측"),
}

SOUND_KEYWORDS = {
    Event.SIREN: ("siren", "emergency vehicle"),
    Event.SCREAM: (
        "screaming",
        "scream",
        "shout",
        "yell",
        "children shouting",
    ),
    Event.HORN: ("car horn", "vehicle horn", "honk", "honking"),
}


def detect_text_event(text: str) -> Event:
    normalized = "".join(text.lower().split())
    for event in (Event.SPEED_WARNING, Event.LEFT_TURN, Event.RIGHT_TURN):
        if any("".join(keyword.split()) in normalized for keyword in TEXT_KEYWORDS[event]):
            return event
    return Event.NONE


def detect_sound_event(
    predictions: Iterable[tuple[str, float]], threshold: float
) -> tuple[Event, str, float]:
    best_event = Event.NONE
    best_label = ""
    best_score = 0.0
    for label, score in predictions:
        for event, keywords in SOUND_KEYWORDS.items():
            if score >= threshold and any(keyword in label.lower() for keyword in keywords):
                if EVENT_PRIORITY[event] > EVENT_PRIORITY[best_event] or (
                    event == best_event and score > best_score
                ):
                    best_event, best_label, best_score = event, label, score
    return best_event, best_label, best_score


def detect_specific_sound_event(
    predictions: Iterable[tuple[str, float]], event: Event, threshold: float
) -> tuple[Event, str, float]:
    best_label = ""
    best_score = 0.0
    for label, score in predictions:
        if score >= threshold and any(
            keyword in label.lower() for keyword in SOUND_KEYWORDS[event]
        ):
            if score > best_score:
                best_label = label
                best_score = score
    return (event, best_label, best_score) if best_label else (Event.NONE, "", 0.0)


def merge_events(stt_event: Event, sound_event: Event) -> Event:
    return max((stt_event, sound_event), key=EVENT_PRIORITY.get)


class ReferenceSoundDetector:
    def __init__(self, path: Path, threshold: float) -> None:
        import numpy as np
        from scipy.io.wavfile import read
        from scipy.signal import resample_poly

        sample_rate, reference = read(path)
        if reference.ndim > 1:
            reference = reference.mean(axis=1)
        if np.issubdtype(reference.dtype, np.integer):
            reference = reference.astype(np.float32) / np.iinfo(reference.dtype).max
        else:
            reference = reference.astype(np.float32)
        if sample_rate != SAMPLE_RATE:
            reference = resample_poly(reference, SAMPLE_RATE, sample_rate)
        self.reference = self._normalize(reference)
        self.threshold = threshold
        logging.info(
            "Loaded reference: %s (%.2fs, threshold %.2f)",
            path,
            len(self.reference) / SAMPLE_RATE,
            threshold,
        )

    @staticmethod
    def _normalize(audio):
        import numpy as np

        audio = np.asarray(audio, dtype=np.float32)
        audio = audio - audio.mean()
        norm = np.linalg.norm(audio)
        return audio / norm if norm > 1e-6 else audio

    def score(self, audio) -> float:
        import numpy as np
        from scipy.signal import correlate, convolve

        audio = self._normalize(audio)
        if len(audio) >= len(self.reference):
            longer = audio
            shorter = self.reference
        else:
            longer = self.reference
            shorter = audio
        correlation = correlate(longer, shorter, mode="valid", method="fft")
        window_energy = convolve(
            np.square(longer),
            np.ones(len(shorter), dtype=np.float32),
            mode="valid",
            method="fft",
        )
        normalized = np.abs(correlation) / np.sqrt(np.maximum(window_energy, 1e-8))
        return float(np.max(normalized)) if normalized.size else 0.0

    def matches(self, audio) -> tuple[bool, float]:
        score = self.score(audio)
        return score >= self.threshold, score


class EventReferenceDetector:
    EVENT_NAMES = {
        Event.LEFT_TURN: ("left", "좌회전"),
        Event.RIGHT_TURN: ("right", "우회전"),
        Event.SPEED_WARNING: ("speed", "속도", "과속"),
    }

    def __init__(self, directory: Path, threshold: float) -> None:
        self.threshold = threshold
        self.detectors: list[tuple[Event, Path, ReferenceSoundDetector]] = []
        for path in sorted(directory.glob("*.wav")):
            event = self._event_from_filename(path.stem.lower())
            if event:
                self.detectors.append((event, path, ReferenceSoundDetector(path, threshold)))
            else:
                logging.warning("Ignoring unrecognized reference filename: %s", path)
        if not self.detectors:
            raise ValueError(f"No recognized WAV references found in {directory}")
        logging.info("Loaded %d event reference files from %s", len(self.detectors), directory)

    @classmethod
    def _event_from_filename(cls, filename: str) -> Event | None:
        for event, names in cls.EVENT_NAMES.items():
            if any(name in filename for name in names):
                return event
        return None

    def detect(self, audio) -> tuple[Event, str, float]:
        best_event = Event.NONE
        best_path = ""
        best_score = 0.0
        for event, path, detector in self.detectors:
            score = detector.score(audio)
            if score > best_score:
                best_event = event
                best_path = path.name
                best_score = score
        if best_score < self.threshold:
            return Event.NONE, best_path, best_score
        return best_event, best_path, best_score


class WhisperTranscriber:
    def __init__(self, model_name: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        logging.info(
            "Loading faster-whisper model: %s (device=%s, compute_type=%s)",
            model_name,
            device,
            compute_type,
        )
        self.model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
        )

    def transcribe(self, audio) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="ko",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class YamnetClassifier:
    def __init__(self) -> None:
        import csv
        import tensorflow_hub as hub

        logging.info("Loading YAMNet from TensorFlow Hub")
        self.model = hub.load("https://tfhub.dev/google/yamnet/1")
        class_map_path = self.model.class_map_path().numpy().decode("utf-8")
        with open(class_map_path, newline="", encoding="utf-8") as class_map:
            self.class_names = [row["display_name"] for row in csv.DictReader(class_map)]

    def classify(self, audio, top_k: int = 521) -> list[tuple[str, float]]:
        import numpy as np
        import tensorflow as tf

        waveform = tf.convert_to_tensor(audio, dtype=tf.float32)
        scores, _, _ = self.model(waveform)
        peak_scores = np.asarray(scores).max(axis=0)
        indices = np.argsort(peak_scores)[::-1][:top_k]
        return [(self.class_names[index], float(peak_scores[index])) for index in indices]
