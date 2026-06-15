#!/usr/bin/env python3
"""Raspberry Pi 4에서 실행되는 스마트 핸들 오디오 클라이언트."""

from __future__ import annotations

"""
Raspberry Pi 코드의 역할:
1. 마이크에서 일정 길이의 오디오를 계속 녹음한다.
2. 녹음한 오디오를 PC AI 서버로 보낸다.
3. PC AI 서버가 반환한 이벤트 결과를 받는다.
4. 이벤트 이름을 Arduino로 USB Serial 전송한다.

Raspberry Pi에서 직접 AI 분석을 하지 않는 이유:
- Whisper와 YAMNet은 무거운 모델이라 Raspberry Pi에서 실시간 처리하기 어렵다.
- Raspberry Pi는 입력/출력 장치 제어에 집중하고, AI 분석은 PC 또는 Colab 서버가 담당한다.
"""

# 실행 옵션을 명령어 인자로 받기 위해 사용한다.
import argparse

# PC AI 서버와 JSON 형식으로 결과를 주고받기 위해 사용한다.
import json

# 실행 상태와 오류를 확인하기 위해 사용한다.
import logging

# API 키를 환경 변수에서 읽기 위해 사용한다.
import os

# 녹음 스레드에서 메인 스레드로 오디오 데이터를 전달하기 위해 사용한다.
import queue

# 녹음을 백그라운드에서 계속 실행하기 위해 사용한다.
import threading

# 시리얼 연결 안정화, 반복 지연, 전송 시간 기록에 사용한다.
import time

# PC AI 서버로 HTTP 요청을 보낼 때 발생하는 오류를 처리하기 위해 사용한다.
import urllib.error

# 별도 HTTP 라이브러리 없이 서버에 POST 요청을 보내기 위해 사용한다.
import urllib.request

# 이벤트 전송 상태를 간단한 클래스로 만들기 위해 사용한다.
from dataclasses import dataclass

# 이벤트 이름을 안전하게 고정된 값으로 관리하기 위해 사용한다.
from enum import Enum

# 디버그 WAV 파일 저장 경로를 다루기 위해 사용한다.
from pathlib import Path


# AI 모델 입력은 16 kHz 오디오를 기준으로 맞춘다.
SAMPLE_RATE = 16_000


class Event(str, Enum):
    """시스템에서 사용할 수 있는 이벤트 명령 목록."""

    # NONE은 아무 알림도 필요 없다는 의미이다.
    NONE = "NONE"

    # 방향 안내 이벤트이다.
    RIGHT_TURN = "RIGHT_TURN"
    LEFT_TURN = "LEFT_TURN"

    # 안전 관련 이벤트이다.
    SPEED_WARNING = "SPEED_WARNING"
    HORN = "HORN"
    SCREAM = "SCREAM"
    SIREN = "SIREN"


class AudioRecorder:
    """마이크에서 오디오를 계속 녹음해 메인 루프에 전달한다."""

    def __init__(
        self,
        seconds: float,
        device: int | str | None = None,
        input_sample_rate: int | None = None,
    ) -> None:
        # sounddevice는 Raspberry Pi의 마이크 입력을 받기 위해 필요하다.
        import sounddevice as sd

        # sounddevice 모듈을 객체에 저장해 다른 메서드에서도 같은 모듈을 사용한다.
        self.sd = sd

        # 한 번에 녹음할 길이이다. 기본값은 1초이다.
        self.seconds = seconds

        # 사용할 마이크 장치 번호 또는 이름이다. None이면 기본 입력 장치를 사용한다.
        self.device = device

        # 선택된 마이크의 기본 샘플레이트를 확인한다.
        device_info = sd.query_devices(device, "input")
        self.input_sample_rate = input_sample_rate or int(device_info["default_samplerate"])

        # 녹음 스레드가 만든 오디오를 메인 루프가 가져가도록 queue를 사용한다.
        # maxsize=2로 작게 둔 이유는 처리 지연이 생겼을 때 오래된 오디오가 쌓이지 않게 하기 위해서이다.
        self.audio_queue: queue.Queue = queue.Queue(maxsize=2)

        # close()가 호출되면 녹음 스레드를 멈추기 위한 이벤트이다.
        self.stopped = threading.Event()

        # 실제 녹음 전에 장치가 요청한 설정을 지원하는지 확인한다.
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

        # 녹음은 백그라운드 스레드에서 계속 돌고, 메인 루프는 서버 통신과 Arduino 전송을 처리한다.
        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()

    def _record_chunk(self):
        """지정한 시간만큼 녹음하고 AI 서버가 쓰는 샘플레이트로 변환한다."""

        # numpy는 오디오 배열 처리, scipy는 샘플레이트 변환에 필요하다.
        import numpy as np
        from scipy.signal import resample_poly

        # 마이크 입력 샘플레이트 기준으로 녹음해야 할 프레임 수를 계산한다.
        frames = int(self.input_sample_rate * self.seconds)

        # channels=1로 모노 오디오를 녹음한다.
        # dtype=float32로 받아야 서버 전송 형식과 맞추기 쉽다.
        audio = self.sd.rec(
            frames,
            samplerate=self.input_sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
        )
        self.sd.wait()

        # sounddevice는 (프레임 수, 채널 수) 형태로 반환하므로 1차원 배열로 바꾼다.
        audio = np.squeeze(audio)

        # 마이크 기본 샘플레이트가 16 kHz가 아니면 AI 서버 기준인 16 kHz로 변환한다.
        if self.input_sample_rate != SAMPLE_RATE:
            audio = resample_poly(audio, SAMPLE_RATE, self.input_sample_rate)

        # 변환 과정에서 길이가 조금 달라질 수 있어 정확히 기대 길이만큼 자른다.
        expected_frames = int(SAMPLE_RATE * self.seconds)
        return np.asarray(audio[:expected_frames], dtype=np.float32)

    def _record_loop(self) -> None:
        """백그라운드 스레드에서 계속 녹음한다."""

        while not self.stopped.is_set():
            audio = self._record_chunk()

            # 처리 속도보다 녹음이 빨라지면 가장 오래된 오디오를 버려 지연을 줄인다.
            if self.audio_queue.full():
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    pass
            self.audio_queue.put(audio)

    def record(self):
        """가장 최근 녹음된 오디오 한 조각을 가져온다."""

        return self.audio_queue.get()

    def close(self) -> None:
        """녹음 스레드를 종료한다."""

        self.stopped.set()
        self.thread.join(timeout=self.seconds + 1.0)


class NetworkAiClient:
    """녹음된 오디오를 PC AI 서버로 보내고 분석 결과를 받는다."""

    def __init__(self, server_url: str, timeout: float, api_key: str | None) -> None:
        self.analyze_url = f"{server_url.rstrip('/')}/analyze"
        self.timeout = timeout
        self.api_key = api_key

    def analyze(self, audio) -> tuple[Event, dict]:
        """float32 오디오 배열을 HTTP POST로 전송하고 이벤트 결과를 반환한다."""

        import numpy as np

        # 서버는 little-endian float32 바이트 배열을 받도록 작성되어 있다.
        payload = np.asarray(audio, dtype="<f4").tobytes()

        # Content-Type은 일반 바이너리 오디오임을 표시한다.
        # X-Sample-Rate는 서버가 입력 샘플레이트를 검증하는 데 사용한다.
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Sample-Rate": str(SAMPLE_RATE),
        }

        # API 키를 사용할 때는 HTTP 헤더로 함께 보낸다.
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        # 표준 라이브러리 urllib로 POST 요청을 만든다.
        request = urllib.request.Request(
            self.analyze_url, data=payload, headers=headers, method="POST"
        )

        # 서버 응답은 JSON 문자열이므로 dict로 변환한다.
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))

        # result["event"]는 문자열이므로 Event enum으로 바꿔 이후 코드에서 안전하게 비교한다.
        return Event(result["event"]), result


class ArduinoSerial:
    """USB 시리얼로 아두이노에 이벤트 명령을 보낸다."""

    def __init__(self, port: str, baudrate: int) -> None:
        # pyserial은 Raspberry Pi와 Arduino의 USB 시리얼 통신에 필요하다.
        import serial

        # timeout/write_timeout을 지정해서 연결 문제가 생겼을 때 무한 대기하지 않도록 한다.
        self.serial = serial.Serial(port, baudrate, timeout=1, write_timeout=1)

        # Arduino는 USB Serial 연결 직후 자동 리셋될 수 있어 잠시 기다린다.
        time.sleep(2.0)

        # 시작 직후 남아 있을 수 있는 입력 버퍼를 비운다.
        self.serial.reset_input_buffer()

    def send(self, event: Event) -> None:
        """아두이노가 읽을 수 있도록 이벤트 이름을 한 줄 문자열로 보낸다."""

        # Arduino 코드는 readStringUntil('\n')으로 한 줄씩 읽으므로 줄바꿈을 붙인다.
        self.serial.write(f"{event.value}\n".encode("ascii"))
        self.serial.flush()

    def close(self) -> None:
        """시리얼 포트를 닫는다."""

        self.serial.close()


@dataclass
class EventSender:
    """같은 이벤트가 계속 반복 전송되지 않도록 관리한다."""

    serial: ArduinoSerial | None
    cooldown_seconds: float
    last_event: Event = Event.NONE
    last_sent_at: float = 0.0

    def send_if_needed(self, event: Event) -> bool:
        """새 이벤트가 있을 때만 아두이노로 전송한다."""

        # NONE은 알림이 없는 상태이므로 Arduino에 보내지 않는다.
        # 대신 last_event를 초기화해 다음 실제 이벤트가 들어오면 다시 보낼 수 있게 한다.
        if event is Event.NONE:
            self.last_event = Event.NONE
            return False

        # 같은 이벤트가 계속 감지되면 Arduino에 반복 전송하지 않는다.
        # Arduino 쪽에서도 패턴을 유지하고 있으므로 같은 명령을 계속 보낼 필요가 없다.
        if event == self.last_event:
            return False

        # --no-serial 옵션이면 실제 Arduino 없이 로그로만 확인한다.
        if self.serial:
            self.serial.send(event)
            logging.info("Sent Arduino command: %s", event.value)
        else:
            logging.info("Serial disabled; command would be: %s", event.value)
        self.last_event = event
        self.last_sent_at = time.monotonic()
        return True


def write_debug_wav(audio, path: Path) -> None:
    """현재 녹음된 오디오를 확인용 WAV 파일로 저장한다."""

    from scipy.io.wavfile import write
    import numpy as np

    # float32 -1.0~1.0 범위 오디오를 일반 WAV에서 많이 쓰는 int16 PCM으로 변환한다.
    pcm = np.int16(np.clip(audio, -1.0, 1.0) * 32767)
    write(path, SAMPLE_RATE, pcm)


def run_self_test() -> None:
    """장치 없이도 기본 데이터 형식이 맞는지 간단히 확인한다."""

    import numpy as np

    # 2초짜리 무음 오디오를 만들고 float32 바이트 길이가 예상과 맞는지 확인한다.
    audio = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    assert len(audio.tobytes()) == SAMPLE_RATE * 2 * 4

    # 서버에서 받은 문자열을 Event enum으로 바꿀 수 있는지 확인한다.
    assert Event("SIREN") is Event.SIREN
    print("Raspberry Pi client self-test passed.")


def parse_device(value: str) -> int | str:
    """마이크 장치 번호는 정수로, 장치 이름은 문자열로 처리한다."""

    return int(value) if value.isdigit() else value


def parse_args() -> argparse.Namespace:
    """Raspberry Pi 클라이언트 실행에 필요한 설정값을 받는다."""

    parser = argparse.ArgumentParser(description=__doc__)

    # 녹음 관련 설정이다.
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--device", type=parse_device)
    parser.add_argument(
        "--input-sample-rate",
        type=int,
        help="Microphone recording rate; defaults to the selected device rate",
    )

    # PC AI 서버 접속 설정이다.
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--server-timeout", type=float, default=60.0)
    parser.add_argument("--api-key", default=os.environ.get("SMART_HANDLE_API_KEY"))

    # Arduino Serial 연결 설정이다.
    parser.add_argument("--serial-port", default="/dev/ttyACM0")
    parser.add_argument("--baudrate", type=int, default=115200)

    # 반복 실행과 디버깅 관련 설정이다.
    parser.add_argument("--cooldown", type=float, default=2.0)
    parser.add_argument("--loop-delay", type=float, default=0.1)
    parser.add_argument("--no-serial", action="store_true")
    parser.add_argument("--debug-wav", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> None:
    """녹음, AI 서버 분석, 아두이노 전송을 반복 실행한다."""

    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.self_test:
        run_self_test()
        return

    # 녹음기, AI 서버 클라이언트, Arduino 연결을 각각 초기화한다.
    recorder = AudioRecorder(args.seconds, args.device, args.input_sample_rate)
    ai_client = NetworkAiClient(args.server_url, args.server_timeout, args.api_key)
    serial_connection = None if args.no_serial else ArduinoSerial(args.serial_port, args.baudrate)
    sender = EventSender(serial_connection, args.cooldown)

    logging.info("Raspberry Pi client started. AI server: %s", args.server_url)
    try:
        while True:
            # 1. 마이크에서 오디오를 가져온다.
            audio = recorder.record()

            # --debug-wav를 지정하면 현재 전송 중인 오디오를 파일로 저장해 마이크 입력을 확인할 수 있다.
            if args.debug_wav:
                write_debug_wav(audio, args.debug_wav)

            try:
                # 2. PC AI 서버에 오디오를 보내 최종 이벤트를 받는다.
                final_event, result = ai_client.analyze(audio)
                logging.info(
                    "text=%r sound=%s(%.3f) final=%s top=%s",
                    result.get("text", ""),
                    result.get("sound_label") or "none",
                    result.get("sound_score", 0.0),
                    final_event.value,
                    result.get("top_predictions", []),
                )

                # 3. 최종 이벤트를 아두이노로 보내 LED와 진동 모터를 제어한다.
                sender.send_if_needed(final_event)
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
                # 네트워크 오류나 서버 응답 형식 오류가 있어도 프로그램을 바로 종료하지 않고 다음 녹음을 계속 시도한다.
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
