# 백엔드 / 파이프라인 코드 동작 설명

이 문서는 발표 자료에 넣을 수 있도록, 코드가 실제로 어떤 순서로 동작하는지를 파일별로 정리한 것이다.

## 전체 실행 흐름

```text
1. raspberry_main.py
   Raspberry Pi가 마이크에서 오디오를 녹음한다.

2. raspberry_main.py
   녹음한 오디오를 PC AI 서버의 /analyze로 전송한다.

3. pc_ai_server.py
   서버가 오디오를 받고 데이터 형식을 검사한다.

4. pc_ai_server.py + ai_logic.py
   YAMNet으로 주변 소리를 분석하고, 필요한 경우 Whisper로 음성을 텍스트로 바꾼다.

5. ai_logic.py
   텍스트 이벤트와 소리 이벤트 중 우선순위가 높은 이벤트를 고른다.

6. raspberry_main.py
   서버가 반환한 최종 이벤트를 Arduino로 Serial 전송한다.

7. arduino_smart_handle.ino
   Arduino가 Serial 명령을 읽고 진동 모터와 LED를 제어한다.
```

---

# 1. raspberry_main.py

`raspberry_main.py`는 Raspberry Pi에서 실행된다. 마이크 녹음, PC 서버 통신, Arduino Serial 전송을 담당한다.

## 1-1. main()에서 전체 장치 초기화

```python
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
```

동작 설명:

```text
args = parse_args()
- 실행할 때 입력한 옵션을 읽는다.
- 예: 서버 주소, 마이크 장치 번호, Arduino 포트, API 키

if args.self_test:
- --self-test 옵션이 있으면 실제 마이크나 Arduino 없이 기본 데이터 형식만 검사하고 종료한다.

recorder = AudioRecorder(...)
- 마이크 녹음 객체를 만든다.
- 이 객체가 생성되면서 내부에서 녹음 스레드가 시작된다.

ai_client = NetworkAiClient(...)
- PC AI 서버와 HTTP 통신할 객체를 만든다.

serial_connection = ...
- --no-serial 옵션이 없으면 Arduino와 USB Serial 연결을 만든다.
- --no-serial 옵션이 있으면 Arduino 없이 로그만 찍는다.

sender = EventSender(...)
- 같은 이벤트를 Arduino에 계속 반복 전송하지 않도록 관리하는 객체이다.
```

## 1-2. 마이크 녹음이 시작되는 부분

```python
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

        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()
```

동작 설명:

```text
import sounddevice as sd
- Raspberry Pi에 연결된 마이크에서 오디오를 받기 위한 라이브러리이다.

device_info = sd.query_devices(device, "input")
- 선택한 마이크 입력 장치의 정보를 읽는다.
- 여기서 장치의 기본 샘플레이트도 확인한다.

self.input_sample_rate = ...
- 사용자가 --input-sample-rate를 직접 지정하면 그 값을 쓴다.
- 지정하지 않으면 마이크 장치의 기본 샘플레이트를 사용한다.

self.audio_queue = queue.Queue(maxsize=2)
- 녹음 스레드와 메인 루프 사이에서 오디오를 전달하는 큐이다.
- maxsize=2로 제한해서 오래된 오디오가 계속 쌓이지 않게 한다.

sd.check_input_settings(...)
- 실제 녹음을 시작하기 전에 마이크가 설정을 지원하는지 검사한다.

self.thread = threading.Thread(...)
- 녹음을 메인 루프와 분리해서 백그라운드에서 계속 실행한다.

self.thread.start()
- _record_loop()가 별도 스레드에서 실행되기 시작한다.
```

## 1-3. 실제 오디오 한 조각을 녹음하는 코드

```python
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
```

동작 설명:

```text
frames = int(self.input_sample_rate * self.seconds)
- 몇 개의 오디오 샘플을 녹음할지 계산한다.
- 예를 들어 샘플레이트가 48000이고 seconds가 1이면 48000개를 녹음한다.

self.sd.rec(...)
- 실제 마이크 녹음을 시작한다.
- channels=1이므로 모노 오디오이다.
- dtype="float32"로 녹음해서 서버로 보내는 형식과 맞춘다.

self.sd.wait()
- 녹음이 끝날 때까지 기다린다.

np.squeeze(audio)
- sounddevice가 반환한 2차원 배열을 1차원 오디오 배열로 바꾼다.

if self.input_sample_rate != SAMPLE_RATE:
- 마이크 샘플레이트가 AI 서버 기준인 16000 Hz와 다르면 변환한다.

resample_poly(audio, SAMPLE_RATE, self.input_sample_rate)
- 오디오를 16 kHz로 리샘플링한다.

expected_frames = int(SAMPLE_RATE * self.seconds)
- 변환 후 기대되는 샘플 개수를 계산한다.

return np.asarray(...)
- 서버로 보낼 최종 오디오 배열을 float32로 반환한다.
```

## 1-4. 녹음 스레드가 계속 도는 코드

```python
def _record_loop(self) -> None:
    while not self.stopped.is_set():
        audio = self._record_chunk()

        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
        self.audio_queue.put(audio)
```

동작 설명:

```text
while not self.stopped.is_set()
- close()가 호출되기 전까지 계속 녹음한다.

audio = self._record_chunk()
- 정해진 길이만큼 오디오를 녹음한다.

if self.audio_queue.full()
- 메인 루프가 서버 응답을 기다리느라 느려질 수 있다.
- 이때 큐가 꽉 차면 오래된 오디오가 남아 있다는 뜻이다.

self.audio_queue.get_nowait()
- 가장 오래된 오디오를 버린다.

self.audio_queue.put(audio)
- 최신 오디오를 큐에 넣는다.
```

이 구조 때문에 서버 응답이 조금 늦어져도 오래된 오디오가 계속 밀리지 않고, 가능한 최신 오디오를 서버로 보낼 수 있다.

## 1-5. PC 서버로 오디오를 보내는 코드

```python
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
```

동작 설명:

```text
np.asarray(audio, dtype="<f4")
- 오디오 배열을 little-endian float32 형식으로 맞춘다.
- <f4는 4바이트 float32라는 뜻이다.

.tobytes()
- numpy 배열을 HTTP로 보낼 수 있는 바이트 데이터로 바꾼다.

Content-Type: application/octet-stream
- 전송 데이터가 일반 텍스트나 JSON이 아니라 바이너리임을 의미한다.

X-Sample-Rate
- 서버가 오디오 샘플레이트를 확인할 수 있도록 보낸다.

X-API-Key
- API 키가 설정된 경우 요청 헤더에 함께 보낸다.

urllib.request.Request(...)
- PC 서버의 /analyze 주소로 POST 요청을 만든다.

json.loads(...)
- 서버가 반환한 JSON 문자열을 Python dict로 변환한다.

Event(result["event"])
- 서버가 준 문자열 이벤트를 Event enum으로 바꾼다.
```

## 1-6. 서버 결과를 Arduino로 보내는 코드

```python
class ArduinoSerial:
    def send(self, event: Event) -> None:
        self.serial.write(f"{event.value}\n".encode("ascii"))
        self.serial.flush()
```

동작 설명:

```text
event.value
- Event.SIREN이면 "SIREN" 문자열이 된다.

f"{event.value}\n"
- Arduino가 한 줄씩 읽을 수 있도록 줄바꿈을 붙인다.

.encode("ascii")
- Arduino로 보낼 바이트 문자열로 바꾼다.

self.serial.write(...)
- USB Serial을 통해 Arduino로 명령을 보낸다.

self.serial.flush()
- 버퍼에 남아 있는 데이터를 즉시 전송한다.
```

```python
def send_if_needed(self, event: Event) -> bool:
    if event is Event.NONE:
        self.last_event = Event.NONE
        return False

    if event == self.last_event:
        return False

    if self.serial:
        self.serial.send(event)
    self.last_event = event
    self.last_sent_at = time.monotonic()
    return True
```

동작 설명:

```text
event is Event.NONE
- 감지된 이벤트가 없으면 Arduino로 보내지 않는다.
- 대신 last_event를 NONE으로 초기화한다.

event == self.last_event
- 같은 이벤트가 연속으로 들어오면 다시 보내지 않는다.
- 예를 들어 SIREN이 계속 감지되어도 Arduino에 SIREN을 반복 전송하지 않는다.

if self.serial
- 실제 Arduino 연결이 있을 때만 전송한다.
- --no-serial 옵션이면 serial이 None이므로 전송하지 않는다.

self.last_event = event
- 방금 보낸 이벤트를 저장해 다음 반복에서 중복 여부를 판단한다.
```

---

# 2. pc_ai_server.py

`pc_ai_server.py`는 PC 또는 Colab에서 실행되는 AI 서버이다. Raspberry Pi에서 받은 오디오를 분석하고 JSON 결과를 반환한다.

## 2-1. 서버 객체 생성과 실행

```python
def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = AiService(
        args.whisper_model,
        args.whisper_device,
        args.whisper_compute_type,
        args.sound_threshold,
        args.speech_threshold,
        args.speech_window_seconds,
        args.scream_threshold,
        args.sound_window_seconds,
        args.hazard_hold_seconds,
        args.voice_rms_threshold,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service, args.api_key))
    server.serve_forever()
```

동작 설명:

```text
args = parse_args()
- 서버 실행 옵션을 읽는다.
- 예: host, port, Whisper 모델 이름, cuda/cpu 여부, threshold 값

service = AiService(...)
- 실제 AI 분석을 담당하는 객체를 만든다.
- 내부에서 Whisper 모델과 YAMNet 모델을 로드한다.

ThreadingHTTPServer(...)
- HTTP 서버를 만든다.
- 요청을 스레드 기반으로 처리할 수 있다.

make_handler(service, args.api_key)
- HTTP 요청 처리 클래스에 AI 분석 객체와 API 키를 연결한다.

server.serve_forever()
- 서버가 종료될 때까지 계속 요청을 기다린다.
```

## 2-2. AiService 초기화

```python
class AiService:
    def __init__(
        self,
        whisper_model: str,
        whisper_device: str,
        whisper_compute_type: str,
        sound_threshold: float,
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
```

동작 설명:

```text
self.transcriber = WhisperTranscriber(...)
- Whisper 모델을 준비한다.
- 한국어 안내 음성을 텍스트로 변환할 때 사용한다.

self.classifier = YamnetClassifier()
- YAMNet 모델을 준비한다.
- 주변 소리를 영어 라벨과 점수로 분류할 때 사용한다.

sound_threshold
- YAMNet 점수가 이 값 이상일 때 위험 소리로 인정한다.

speech_threshold
- YAMNet이 사람 음성이라고 판단한 점수가 이 값 이상이면 Whisper를 실행할 수 있다.

scream_threshold
- 비명은 더 민감하게 감지하기 위해 별도 기준을 둔다.

speech_window_samples
- Whisper에 넣을 누적 오디오 길이를 샘플 개수로 변환한 값이다.

sound_window_samples
- YAMNet에 넣을 최근 오디오 길이를 샘플 개수로 변환한 값이다.

hazard_hold_seconds
- 위험 이벤트를 몇 초간 유지할지 정하는 값이다.

voice_rms_threshold
- 음량 기준으로 사람 음성이 있을 가능성을 판단하는 보조 기준이다.
```

## 2-3. /analyze 요청 처리

```python
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
```

동작 설명:

```text
self.path != "/analyze"
- POST 요청 중 /analyze만 허용한다.

api_key 검사
- 서버에 API 키가 설정되어 있으면 요청 헤더의 X-API-Key와 비교한다.

X-Sample-Rate 검사
- Raspberry Pi가 보낸 오디오가 16000 Hz인지 확인한다.

Content-Length 읽기
- 요청 본문에 들어 있는 오디오 바이트 길이를 확인한다.

length <= 0
- 빈 오디오 요청을 막는다.

length > SAMPLE_RATE * 4 * 30
- 30초보다 긴 오디오를 막는다.
- SAMPLE_RATE는 16000, float32는 4바이트이므로 30초 기준으로 제한한다.

length % 4 != 0
- float32는 샘플 하나가 4바이트이므로 전체 길이가 4로 나누어 떨어져야 한다.

np.frombuffer(..., dtype="<f4")
- HTTP 본문 바이트를 float32 numpy 배열로 해석한다.

service.analyze(...)
- 변환된 오디오 배열을 AI 분석 함수로 넘긴다.

self.send_json(200, result)
- 분석 결과를 JSON으로 Raspberry Pi에 돌려준다.
```

## 2-4. 분석용 오디오 버퍼

```python
def append_speech_audio(self, audio):
    import numpy as np

    with self.speech_buffer_lock:
        if self.speech_buffer is None:
            self.speech_buffer = np.asarray(audio, dtype=np.float32)
        else:
            self.speech_buffer = np.concatenate((self.speech_buffer, audio))
        self.speech_buffer = self.speech_buffer[-self.speech_window_samples :]
        return self.speech_buffer.copy()
```

동작 설명:

```text
speech_buffer
- Whisper용 오디오 버퍼이다.
- 안내 문장을 인식하려면 짧은 순간보다 어느 정도 문맥이 필요하므로 최근 오디오를 누적한다.

np.concatenate(...)
- 새 오디오를 기존 버퍼 뒤에 이어 붙인다.

self.speech_buffer[-self.speech_window_samples :]
- 너무 긴 오디오는 버리고 최근 구간만 남긴다.

copy()
- lock 밖에서 분석해도 원본 버퍼가 바뀌지 않도록 복사본을 반환한다.
```

```python
def append_sound_audio(self, audio):
    import numpy as np

    with self.sound_buffer_lock:
        if self.sound_buffer is None:
            self.sound_buffer = np.asarray(audio, dtype=np.float32)
        else:
            self.sound_buffer = np.concatenate((self.sound_buffer, audio))
        self.sound_buffer = self.sound_buffer[-self.sound_window_samples :]
        return self.sound_buffer.copy()
```

동작 설명:

```text
sound_buffer
- YAMNet용 오디오 버퍼이다.
- 사이렌, 경적, 비명은 짧은 소리도 중요하므로 speech_buffer보다 짧게 운용한다.

두 버퍼를 나눈 이유
- Whisper는 문장 인식용이라 긴 문맥이 필요하다.
- YAMNet은 위험 소리 감지용이라 빠른 반응이 필요하다.
```

## 2-5. AI 분석 메인 흐름

```python
def analyze(self, audio) -> dict:
    speech_audio = self.append_speech_audio(audio)
    sound_audio = self.append_sound_audio(audio)
    sound_future = self.executor.submit(self.classify_sound, sound_audio)
    sound_event, sound_label, sound_score, predictions = sound_future.result()
    sound_event, sound_label, sound_score = self.hold_hazard(
        sound_event, sound_label, sound_score
    )
```

동작 설명:

```text
speech_audio = ...
- Whisper에 사용할 최근 음성 버퍼를 만든다.

sound_audio = ...
- YAMNet에 사용할 최근 소리 버퍼를 만든다.

self.executor.submit(...)
- YAMNet 소리 분류를 별도 작업으로 실행한다.

sound_future.result()
- YAMNet 분석 결과를 가져온다.

hold_hazard(...)
- 감지된 위험 이벤트를 일정 시간 유지하도록 보정한다.
```

```python
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
text, stt_event = self.transcribe(
    speech_audio,
    sound_event is Event.NONE
    and (
        speech_score >= self.speech_threshold
        or voice_rms >= self.voice_rms_threshold
    ),
)
```

동작 설명:

```text
voice_labels
- YAMNet 라벨 중 사람 말소리에 해당하는 라벨 목록이다.

speech_score
- YAMNet 결과에서 사람 음성 관련 점수 중 가장 높은 값이다.

voice_rms
- 오디오의 평균 음량을 계산한 값이다.
- YAMNet이 speech 라벨을 낮게 줘도 실제 음량이 있으면 Whisper를 실행할 수 있게 한다.

sound_event is Event.NONE
- 사이렌, 경적, 비명 같은 위험 소리가 감지되면 Whisper를 기다리지 않는다.
- 위험 소리를 우선 처리하기 위한 조건이다.

speech_score >= self.speech_threshold or voice_rms >= self.voice_rms_threshold
- 사람 음성이 있을 가능성이 있을 때만 Whisper를 실행한다.
- Whisper는 무거운 모델이므로 매번 실행하지 않는다.
```

```python
if stt_event is not Event.NONE:
    self.clear_speech_audio()

final_event = merge_events(stt_event, sound_event)
return {
    "event": final_event.value,
    "text": text,
    "stt_event": stt_event.value,
    "sound_event": sound_event.value,
    "sound_label": sound_label,
    "sound_score": sound_score,
}
```

동작 설명:

```text
if stt_event is not Event.NONE
- 좌회전, 우회전, 과속 같은 음성 이벤트가 감지된 경우이다.

self.clear_speech_audio()
- 같은 안내 문장이 다음 요청에서도 반복 감지되지 않도록 음성 버퍼를 비운다.

merge_events(stt_event, sound_event)
- 음성 이벤트와 소리 이벤트 중 우선순위가 높은 것을 최종 이벤트로 선택한다.

return {...}
- Raspberry Pi가 받을 JSON 응답을 만든다.
- event가 Arduino로 전달될 최종 이벤트이다.
```

## 2-6. 위험 이벤트 유지

```python
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
```

동작 설명:

```text
now = time.monotonic()
- 현재 시간을 가져온다.
- 이벤트 유지 시간이 끝났는지 계산하는 데 사용한다.

if event is not Event.NONE
- 새 위험 소리가 감지된 경우이다.

self.held_sound_event is Event.NONE
- 현재 유지 중인 위험 이벤트가 없으면 새 이벤트를 저장한다.

event == self.held_sound_event
- 같은 이벤트가 다시 감지되면 점수와 유지 시간을 갱신한다.

EVENT_PRIORITY[event] > EVENT_PRIORITY[self.held_sound_event]
- 더 중요한 위험 이벤트가 들어오면 새 이벤트로 바꾼다.

self.held_sound_until = now + self.hazard_hold_seconds
- 현재 시간부터 hazard_hold_seconds만큼 이벤트를 유지한다.

if now < self.held_sound_until
- 유지 시간이 남아 있으면 이전 위험 이벤트를 계속 반환한다.

유지 시간이 끝나면
- held_sound_event, held_sound_label, held_sound_score를 초기화한다.
```

---

# 3. ai_logic.py

`ai_logic.py`는 모델 출력값을 실제 시스템 이벤트로 변환한다.

## 3-1. 텍스트 이벤트 판단

```python
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
```

동작 설명:

```text
TEXT_KEYWORDS
- Whisper가 반환한 텍스트에서 찾을 단어 목록이다.

Event.SPEED_WARNING
- 속도위반, 과속, 제한속도 같은 단어가 들어 있으면 과속 경고로 판단한다.

Event.LEFT_TURN
- 좌회전, 왼쪽, 좌측이 들어 있으면 왼쪽 방향 안내로 판단한다.

Event.RIGHT_TURN
- 우회전, 오른쪽, 우측이 들어 있으면 오른쪽 방향 안내로 판단한다.
```

```python
def detect_text_event(text: str) -> Event:
    normalized = "".join(text.lower().split())
    for event in (Event.SPEED_WARNING, Event.LEFT_TURN, Event.RIGHT_TURN):
        if any("".join(keyword.split()) in normalized for keyword in TEXT_KEYWORDS[event]):
            return event
    return Event.NONE
```

동작 설명:

```text
text.lower()
- 영어가 섞였을 때 대소문자 차이를 없앤다.

split()
- 공백 기준으로 문자열을 나눈다.

"".join(...)
- 나뉜 문자열을 다시 붙여 공백을 제거한다.

for event in (...)
- 과속 경고, 좌회전, 우회전 순서로 검사한다.

any(...)
- 해당 이벤트의 키워드 중 하나라도 문장 안에 있으면 True가 된다.

return event
- 키워드가 발견되면 해당 이벤트를 반환한다.

return Event.NONE
- 아무 키워드도 없으면 알림 없음으로 반환한다.
```

## 3-2. YAMNet 소리 이벤트 판단

```python
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
```

동작 설명:

```text
YAMNet은 한국어가 아니라 영어 라벨을 반환한다.
그래서 위험 소리 판단에는 영어 키워드를 사용한다.

siren, emergency vehicle
- 사이렌 또는 긴급 차량 소리로 판단한다.

car horn, vehicle horn, honk, honking
- 경적 소리로 판단한다.

scream, screaming, shout, yell
- 비명 또는 외침으로 판단한다.
```

```python
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
```

동작 설명:

```text
predictions
- YAMNet이 반환한 (라벨, 점수) 목록이다.

best_event, best_label, best_score
- 현재까지 찾은 가장 중요한 이벤트와 그 근거 라벨/점수이다.

for label, score in predictions
- YAMNet 예측 결과를 하나씩 검사한다.

score >= threshold
- 점수가 기준 이상일 때만 이벤트 후보로 인정한다.

keyword in label.lower()
- 라벨 안에 siren, honk, scream 같은 단어가 들어 있는지 확인한다.

EVENT_PRIORITY 비교
- 여러 위험 소리가 동시에 후보가 되면 더 중요한 이벤트를 선택한다.

event == best_event and score > best_score
- 같은 이벤트라면 점수가 더 높은 라벨을 선택한다.

return best_event, best_label, best_score
- 최종 소리 이벤트와 라벨, 점수를 반환한다.
```

## 3-3. 이벤트 우선순위 병합

```python
EVENT_PRIORITY = {
    Event.NONE: 0,
    Event.RIGHT_TURN: 1,
    Event.LEFT_TURN: 2,
    Event.SPEED_WARNING: 3,
    Event.HORN: 4,
    Event.SCREAM: 5,
    Event.SIREN: 6,
}
```

동작 설명:

```text
숫자가 클수록 더 중요한 이벤트이다.

SIREN
- 가장 높은 우선순위이다.

SCREAM
- 비명도 위험 상황이므로 높은 우선순위이다.

HORN
- 경적은 주변 차량 위험을 의미한다.

SPEED_WARNING
- 과속 경고는 방향 안내보다 중요하다.

LEFT_TURN / RIGHT_TURN
- 방향 안내는 안전 위험 소리보다 낮은 우선순위이다.

NONE
- 아무 이벤트도 없는 상태이다.
```

```python
def merge_events(stt_event: Event, sound_event: Event) -> Event:
    return max((stt_event, sound_event), key=EVENT_PRIORITY.get)
```

동작 설명:

```text
stt_event
- Whisper 텍스트 분석으로 나온 이벤트이다.

sound_event
- YAMNet 소리 분석으로 나온 이벤트이다.

max(..., key=EVENT_PRIORITY.get)
- 두 이벤트 중 EVENT_PRIORITY 값이 더 큰 것을 고른다.

예시
- stt_event = LEFT_TURN
- sound_event = SIREN
- SIREN의 우선순위가 더 높으므로 SIREN을 반환한다.
```

## 3-4. Whisper 모델 실행

```python
class WhisperTranscriber:
    def __init__(self, model_name: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

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
```

동작 설명:

```text
WhisperModel(...)
- faster-whisper 모델을 로드한다.

model_name
- small, medium 같은 모델 크기이다.

device
- cpu 또는 cuda이다.

compute_type
- cpu에서는 int8, GPU에서는 float16 등을 사용할 수 있다.

language="ko"
- 한국어 음성으로 인식하도록 지정한다.

beam_size=1
- 속도를 우선한 설정이다.

vad_filter=True
- 말소리가 아닌 구간을 어느 정도 걸러 준다.

condition_on_previous_text=False
- 이전 인식 결과에 지나치게 영향을 받지 않도록 한다.

segments
- Whisper가 나눈 텍스트 조각들이다.

" ".join(...)
- 여러 segment의 텍스트를 하나의 문장으로 합친다.
```

## 3-5. YAMNet 모델 실행

```python
class YamnetClassifier:
    def __init__(self) -> None:
        import csv
        import tensorflow_hub as hub

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
```

동작 설명:

```text
hub.load(...)
- TensorFlow Hub에서 YAMNet 모델을 불러온다.

class_map_path
- YAMNet 라벨 번호를 실제 이름으로 바꾸기 위한 CSV 파일 경로이다.

self.class_names
- 인덱스 번호를 사람이 읽을 수 있는 라벨 이름으로 바꾸기 위해 저장한다.

tf.convert_to_tensor(audio, dtype=tf.float32)
- numpy 오디오 배열을 TensorFlow tensor로 바꾼다.

scores, _, _ = self.model(waveform)
- YAMNet 모델에 오디오를 넣어 라벨별 점수를 얻는다.

np.asarray(scores).max(axis=0)
- YAMNet은 시간 구간별 점수를 반환한다.
- 그중 라벨별 최대 점수를 사용해서 짧게 발생한 소리도 잡는다.

np.argsort(peak_scores)[::-1]
- 점수가 높은 라벨 순서로 정렬한다.

return [...]
- 라벨 이름과 점수를 튜플 목록으로 반환한다.
```

---

# 4. arduino_smart_handle.ino

`arduino_smart_handle.ino`는 Arduino에서 실행된다. Raspberry Pi가 보낸 문자열 명령을 실제 진동 모터와 LED 출력으로 바꾼다.

## 4-1. 핀과 패턴 상태

```cpp
const uint8_t LEFT_MOTOR_PIN = 4;
const uint8_t RIGHT_MOTOR_PIN = 2;
const uint8_t LED_PIN = 6;
const uint16_t LED_COUNT = 16;
const unsigned long SERIAL_BAUDRATE = 115200;
```

동작 설명:

```text
LEFT_MOTOR_PIN
- 왼쪽 진동 모터를 제어하는 Arduino 핀이다.

RIGHT_MOTOR_PIN
- 오른쪽 진동 모터를 제어하는 Arduino 핀이다.

LED_PIN
- NeoPixel LED 데이터 핀이다.

LED_COUNT
- LED 링에 있는 LED 개수이다.

SERIAL_BAUDRATE
- Raspberry Pi와 Arduino가 같은 속도로 Serial 통신하기 위한 값이다.
```

```cpp
struct FeedbackPattern {
  uint8_t red;
  uint8_t green;
  uint8_t blue;
  unsigned long onMs;
  unsigned long offMs;
  uint8_t repetitions;
  uint8_t motorMask;
  unsigned long ledHoldMs;
};
```

동작 설명:

```text
FeedbackPattern
- 하나의 알림 패턴을 표현하는 구조체이다.

red, green, blue
- LED 색상값이다.

onMs
- 모터가 켜져 있는 시간이다.

offMs
- 모터가 꺼져 있는 시간이다.

repetitions
- on/off 패턴을 몇 번 반복할지 나타낸다.

motorMask
- 어떤 모터를 사용할지 나타내는 비트값이다.
- 0x01: 왼쪽
- 0x02: 오른쪽
- 0x03: 양쪽

ledHoldMs
- LED를 유지할 전체 시간이다.
```

## 4-2. LED와 모터 제어

```cpp
void setLed(uint8_t red, uint8_t green, uint8_t blue) {
  strip.fill(strip.Color(red, green, blue));
  strip.show();
}
```

동작 설명:

```text
strip.Color(red, green, blue)
- RGB 값을 NeoPixel이 이해하는 색상값으로 바꾼다.

strip.fill(...)
- LED 링 전체를 같은 색으로 채운다.

strip.show()
- 설정한 색상을 실제 LED에 반영한다.
```

```cpp
void setMotors(uint8_t mask) {
  digitalWrite(LEFT_MOTOR_PIN, (mask & 0x01) ? HIGH : LOW);
  digitalWrite(RIGHT_MOTOR_PIN, (mask & 0x02) ? HIGH : LOW);
}
```

동작 설명:

```text
mask & 0x01
- mask의 첫 번째 비트가 켜져 있으면 왼쪽 모터를 켠다.

mask & 0x02
- mask의 두 번째 비트가 켜져 있으면 오른쪽 모터를 켠다.

HIGH
- 모터를 켠다.

LOW
- 모터를 끈다.
```

## 4-3. 양쪽 모터를 번갈아 켜는 코드

```cpp
void setPatternMotors(uint8_t mask, unsigned long elapsed) {
  if (mask == 0x03) {
    setMotors(((elapsed / MOTOR_ALTERNATE_MS) % 2 == 0) ? 0x01 : 0x02);
  } else {
    setMotors(mask);
  }
}
```

동작 설명:

```text
mask == 0x03
- 양쪽 모터를 모두 사용해야 하는 경우이다.

elapsed / MOTOR_ALTERNATE_MS
- 현재 경과 시간을 250ms 단위로 나눈다.

% 2 == 0
- 짝수 구간이면 왼쪽 모터를 켠다.

? 0x01 : 0x02
- 짝수 구간은 왼쪽, 홀수 구간은 오른쪽 모터를 선택한다.

else
- 한쪽 모터만 사용하는 경우에는 그대로 켠다.
```

양쪽 모터를 동시에 계속 켜지 않고 번갈아 켜는 이유는 순간 전류 부담을 줄이기 위해서이다.

## 4-4. Serial 명령 처리

```cpp
void handleCommand(String command) {
  command.trim();

  if (patternActive && command == activeCommand) {
    return;
  }

  if (patternActive && millis() - patternStartedAt < MIN_PATTERN_HOLD_MS) {
    return;
  }

  if (patternActive && activePriority >= 3 && commandPriority(command) < activePriority) {
    return;
  }

  if (command == "LEFT_TURN") {
    startPattern(command, {0, 0, 0, TURN_VIBRATION_MS, 0, 1, 0x01, TURN_VIBRATION_MS});
  } else if (command == "RIGHT_TURN") {
    startPattern(command, {0, 0, 0, TURN_VIBRATION_MS, 0, 1, 0x02, TURN_VIBRATION_MS});
  } else if (command == "SIREN") {
    startPattern(command, {0, 0, 255, ALERT_VIBRATION_MS, 0, 1, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "HORN") {
    startPattern(command, {255, 180, 0, 500, 500, 8, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "SCREAM") {
    startPattern(command, {255, 0, 0, 200, 150, 23, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "SPEED_WARNING") {
    startPattern(command, {255, 80, 0, 900, 600, 6, 0x03, ALERT_LED_HOLD_MS});
  }
}
```

동작 설명:

```text
command.trim()
- Raspberry Pi가 보낸 문자열 끝의 줄바꿈을 제거한다.

patternActive && command == activeCommand
- 현재 실행 중인 명령과 같은 명령이면 무시한다.
- 같은 패턴이 계속 재시작되는 것을 막는다.

millis() - patternStartedAt < MIN_PATTERN_HOLD_MS
- 현재 패턴이 시작된 지 최소 유지 시간이 지나지 않았으면 새 명령을 무시한다.
- 알림이 너무 짧게 끊기지 않게 한다.

activePriority >= 3
- 현재 실행 중인 알림이 과속 경고 이상의 안전 이벤트인지 확인한다.

commandPriority(command) < activePriority
- 새 명령의 우선순위가 현재 알림보다 낮으면 무시한다.

LEFT_TURN
- 왼쪽 모터만 켠다.

RIGHT_TURN
- 오른쪽 모터만 켠다.

SIREN
- 파란 LED와 긴 진동을 실행한다.

HORN
- 노란 LED와 짧은 반복 진동을 실행한다.

SCREAM
- 빨간 LED와 빠른 반복 진동을 실행한다.

SPEED_WARNING
- 주황 LED와 느린 반복 진동을 실행한다.
```

## 4-5. delay 없이 패턴 갱신

```cpp
void loop() {
  if (Serial.available() > 0) {
    handleCommand(Serial.readStringUntil('\n'));
  }
  updatePattern();
}
```

동작 설명:

```text
Serial.available() > 0
- Raspberry Pi에서 새 명령이 들어왔는지 확인한다.

Serial.readStringUntil('\n')
- 줄바꿈이 나올 때까지 문자열을 읽는다.

handleCommand(...)
- 읽은 문자열을 LED/진동 패턴으로 변환한다.

updatePattern()
- 현재 실행 중인 패턴의 시간 상태를 갱신한다.

loop()
- 이 과정이 계속 반복된다.
```

```cpp
void updatePattern() {
  if (!patternActive) {
    return;
  }

  const unsigned long elapsed = millis() - patternStartedAt;
  const unsigned long cycleMs = activePattern.onMs + activePattern.offMs;
  const unsigned long vibrationEndMs =
      activePattern.repetitions * activePattern.onMs +
      (activePattern.repetitions - 1) * activePattern.offMs;

  if (elapsed < vibrationEndMs && cycleMs > 0) {
    if ((elapsed % cycleMs) < activePattern.onMs) {
      setPatternMotors(activePattern.motorMask, elapsed);
    } else {
      setMotors(0);
    }
  } else {
    setMotors(0);
  }
}
```

동작 설명:

```text
if (!patternActive)
- 실행 중인 패턴이 없으면 아무것도 하지 않는다.

elapsed = millis() - patternStartedAt
- 현재 패턴이 시작된 뒤 지난 시간을 계산한다.

cycleMs = onMs + offMs
- 진동 한 주기의 길이를 계산한다.

vibrationEndMs
- 전체 진동 반복이 끝나는 시간을 계산한다.

elapsed < vibrationEndMs
- 아직 진동해야 하는 시간인지 확인한다.

elapsed % cycleMs
- 현재 시간이 반복 주기 안에서 어느 위치인지 계산한다.

< activePattern.onMs
- 현재가 on 구간이면 모터를 켠다.

else setMotors(0)
- off 구간이면 모터를 끈다.

delay()를 쓰지 않는 이유
- delay()를 쓰면 그 시간 동안 새 Serial 명령을 읽을 수 없다.
- millis()를 사용하면 loop()가 계속 돌면서 새 명령 수신과 패턴 갱신을 동시에 처리할 수 있다.
```

---

# 5. 정리

```text
raspberry_main.py
- 오디오를 녹음하고 서버로 보내며, 최종 이벤트를 Arduino로 보낸다.

pc_ai_server.py
- 오디오를 받아 AI 분석을 실행하고 JSON 결과를 반환한다.

ai_logic.py
- Whisper와 YAMNet 결과를 실제 이벤트로 변환한다.

arduino_smart_handle.ino
- 이벤트 문자열을 진동 모터와 LED 출력으로 바꾼다.
```
