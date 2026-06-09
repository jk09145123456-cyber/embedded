#!/usr/bin/env python3
"""Smart handle audio client for Raspberry Pi 4."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


SAMPLE_RATE = 16_000


class Event(str, Enum):
    NONE = "NONE"
    RIGHT_TURN = "RIGHT_TURN"
    LEFT_TURN = "LEFT_TURN"
    SPEED_WARNING = "SPEED_WARNING"
    HORN = "HORN"
    SCREAM = "SCREAM"
    SIREN = "SIREN"


class AudioRecorder:
    def __init__(
        self,
        seconds: float,
        device: int | str | None = None,
        input_sample_rate: int | None = None,
    ) -> None:
        import sounddevice as sd

        self.sd = sd
        self.seconds = seconds
        self.device = device
        device_info = sd.query_devices(device, "input")
        self.input_sample_rate = input_sample_rate or int(device_info["default_samplerate"])
        self.audio_queue: queue.Queue = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        sd.check_input_settings(
            device=device,
            channels=1,
            dtype="float32",
            samplerate=self.input_sample_rate,
        )
        logging.info(
            "Microphone: %s, input sample rate: %d Hz, AI sample rate: %d Hz",
            device_info["name"],
            self.input_sample_rate,
            SAMPLE_RATE,
        )
        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()

    def _record_chunk(self):
        import numpy as np
        from scipy.signal import resample_poly

        frames = int(self.input_sample_rate * self.seconds)
        audio = self.sd.rec(
            frames,
            samplerate=self.input_sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
        )
        self.sd.wait()
        audio = np.squeeze(audio)
        if self.input_sample_rate != SAMPLE_RATE:
            audio = resample_poly(audio, SAMPLE_RATE, self.input_sample_rate)
        expected_frames = int(SAMPLE_RATE * self.seconds)
        return np.asarray(audio[:expected_frames], dtype=np.float32)

    def _record_loop(self) -> None:
        while not self.stopped.is_set():
            audio = self._record_chunk()
            if self.audio_queue.full():
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    pass
            self.audio_queue.put(audio)

    def record(self):
        return self.audio_queue.get()

    def close(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=self.seconds + 1.0)


class NetworkAiClient:
    def __init__(self, server_url: str, timeout: float, api_key: str | None) -> None:
        self.analyze_url = f"{server_url.rstrip('/')}/analyze"
        self.timeout = timeout
        self.api_key = api_key

    def analyze(self, audio) -> tuple[Event, dict]:
        import numpy as np

        payload = np.asarray(audio, dtype="<f4").tobytes()
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Sample-Rate": str(SAMPLE_RATE),
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        request = urllib.request.Request(
            self.analyze_url, data=payload, headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return Event(result["event"]), result


class ArduinoSerial:
    def __init__(self, port: str, baudrate: int) -> None:
        import serial

        self.serial = serial.Serial(port, baudrate, timeout=1, write_timeout=1)
        time.sleep(2.0)
        self.serial.reset_input_buffer()

    def send(self, event: Event) -> None:
        self.serial.write(f"{event.value}\n".encode("ascii"))
        self.serial.flush()

    def close(self) -> None:
        self.serial.close()


@dataclass
class EventSender:
    serial: ArduinoSerial | None
    cooldown_seconds: float
    last_event: Event = Event.NONE
    last_sent_at: float = 0.0

    def send_if_needed(self, event: Event) -> bool:
        if event is Event.NONE:
            self.last_event = Event.NONE
            return False

        if event == self.last_event:
            return False

        if self.serial:
            self.serial.send(event)
            logging.info("Sent Arduino command: %s", event.value)
        else:
            logging.info("Serial disabled; command would be: %s", event.value)
        self.last_event = event
        self.last_sent_at = time.monotonic()
        return True


def write_debug_wav(audio, path: Path) -> None:
    from scipy.io.wavfile import write
    import numpy as np

    pcm = np.int16(np.clip(audio, -1.0, 1.0) * 32767)
    write(path, SAMPLE_RATE, pcm)


def run_self_test() -> None:
    import numpy as np

    audio = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    assert len(audio.tobytes()) == SAMPLE_RATE * 2 * 4
    assert Event("SIREN") is Event.SIREN
    print("Raspberry Pi client self-test passed.")


def parse_device(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--device", type=parse_device)
    parser.add_argument(
        "--input-sample-rate",
        type=int,
        help="Microphone recording rate; defaults to the selected device rate",
    )
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--server-timeout", type=float, default=60.0)
    parser.add_argument("--api-key", default=os.environ.get("SMART_HANDLE_API_KEY"))
    parser.add_argument("--serial-port", default="/dev/ttyACM0")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--cooldown", type=float, default=2.0)
    parser.add_argument("--loop-delay", type=float, default=0.1)
    parser.add_argument("--no-serial", action="store_true")
    parser.add_argument("--debug-wav", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.self_test:
        run_self_test()
        return

    recorder = AudioRecorder(args.seconds, args.device, args.input_sample_rate)
    ai_client = NetworkAiClient(args.server_url, args.server_timeout, args.api_key)
    serial_connection = None if args.no_serial else ArduinoSerial(args.serial_port, args.baudrate)
    sender = EventSender(serial_connection, args.cooldown)

    logging.info("Raspberry Pi client started. AI server: %s", args.server_url)
    try:
        while True:
            audio = recorder.record()
            if args.debug_wav:
                write_debug_wav(audio, args.debug_wav)

            try:
                final_event, result = ai_client.analyze(audio)
                logging.info(
                    "text=%r sound=%s(%.3f) reference=%s(%.3f, %s) final=%s top=%s",
                    result.get("text", ""),
                    result.get("sound_label") or "none",
                    result.get("sound_score", 0.0),
                    result.get("reference_event", Event.NONE.value),
                    result.get("reference_score", 0.0),
                    result.get("reference_name") or "none",
                    final_event.value,
                    result.get("top_predictions", []),
                )
                sender.send_if_needed(final_event)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
                logging.error("AI server request failed: %s", error)
            time.sleep(args.loop_delay)
    except KeyboardInterrupt:
        logging.info("Stopping")
    finally:
        recorder.close()
        if serial_connection:
            serial_connection.close()


if __name__ == "__main__":
    main()
