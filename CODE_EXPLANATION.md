# 스마트 핸들 시스템 코드 설명

이 문서는 제출 폴더에 들어 있는 실행 코드 4개만 설명한다.

```text
arduino_smart_handle.ino
raspberry_main.py
pc_ai_server.py
ai_logic.py
```

## 1. 전체 동작 흐름

```text
마이크
  -> Raspberry Pi(raspberry_main.py)
  -> PC AI 서버(pc_ai_server.py)
  -> AI 판단(ai_logic.py)
  -> Raspberry Pi(raspberry_main.py)
  -> Arduino(arduino_smart_handle.ino)
  -> 진동 모터 / LED
```

Raspberry Pi는 마이크 입력과 Arduino 통신을 담당하고, PC 서버는 AI 분석을 담당한다. Arduino는 최종 이벤트 명령을 받아 실제 출력 장치인 진동 모터와 LED를 제어한다.

## 2. arduino_smart_handle.ino

Arduino에서 실행되는 코드이다.

주요 역할:

- Raspberry Pi가 USB Serial로 보내는 이벤트 명령을 읽는다.
- `LEFT_TURN`, `RIGHT_TURN`, `SIREN`, `HORN`, `SCREAM`, `SPEED_WARNING` 명령을 구분한다.
- 명령에 맞춰 진동 모터와 NeoPixel LED를 제어한다.
- `delay()` 대신 `millis()`를 사용해 새 명령을 계속 받을 수 있게 한다.

핀 설정:

```text
왼쪽 진동 모터: D4
오른쪽 진동 모터: D2
NeoPixel LED 데이터 핀: D6
NeoPixel LED 개수: 16
Serial 속도: 115200
```

중요한 함수:

```text
setLed()
- NeoPixel LED 링 전체 색상을 바꾼다.

setMotors()
- 왼쪽/오른쪽 진동 모터를 켜고 끈다.

startPattern()
- 새 알림 패턴을 시작한다.

updatePattern()
- 시간이 지나면서 진동 반복과 LED 유지 시간을 갱신한다.

handleCommand()
- Raspberry Pi에서 받은 문자열 명령을 실제 출력 패턴으로 바꾼다.
```

LED는 일반 LED처럼 `digitalWrite()`만으로 색상을 제어할 수 없다. NeoPixel LED 링은 하나의 데이터 핀으로 여러 LED의 색상 데이터를 보내야 하므로 `Adafruit_NeoPixel` 라이브러리를 사용한다.

## 3. raspberry_main.py

Raspberry Pi에서 실행되는 코드이다.

주요 역할:

- 마이크로 오디오를 계속 녹음한다.
- 녹음한 오디오를 PC AI 서버의 `/analyze` API로 보낸다.
- 서버가 반환한 최종 이벤트를 받는다.
- 이벤트를 Arduino에 USB Serial로 전송한다.

중요한 클래스:

```text
AudioRecorder
- 백그라운드 스레드에서 마이크 오디오를 계속 녹음한다.
- AI 서버 기준인 16 kHz로 샘플레이트를 맞춘다.

NetworkAiClient
- 녹음한 오디오를 HTTP POST 요청으로 PC AI 서버에 보낸다.
- 서버의 JSON 응답에서 최종 이벤트를 읽는다.

ArduinoSerial
- Arduino에 이벤트 이름을 한 줄 문자열로 보낸다.

EventSender
- 같은 이벤트가 계속 반복 전송되지 않도록 막는다.
```

Arduino로 보내는 명령 예시:

```text
LEFT_TURN
RIGHT_TURN
SIREN
HORN
SCREAM
SPEED_WARNING
```

## 4. pc_ai_server.py

PC 또는 Colab에서 실행되는 AI 분석 서버 코드이다.

주요 역할:

- Raspberry Pi가 보낸 오디오 바이트를 HTTP로 받는다.
- 오디오 형식과 샘플레이트가 맞는지 검사한다.
- Whisper와 YAMNet을 이용해 음성 안내와 주변 위험 소리를 분석한다.
- 최종 이벤트를 JSON으로 반환한다.

주요 API:

```text
GET /health
- 서버가 살아 있는지 확인한다.

POST /analyze
- Raspberry Pi가 오디오를 보내는 분석 API이다.
```

중요한 클래스:

```text
AiService
- Whisper 음성 인식, YAMNet 소리 분류, 이벤트 병합을 담당한다.

Handler
- HTTP 요청을 받아 JSON 응답을 보내는 내부 클래스이다.
```

서버 응답 예시:

```json
{
  "event": "SIREN",
  "text": "",
  "stt_event": "NONE",
  "sound_event": "SIREN",
  "sound_label": "siren",
  "sound_score": 0.82,
  "speech_score": 0.01,
  "voice_rms": 0.004,
  "top_predictions": []
}
```

## 5. ai_logic.py

AI 판단 로직을 모아 둔 파일이다.

주요 역할:

- Whisper가 인식한 한국어 문장에서 좌회전, 우회전, 과속 경고 키워드를 찾는다.
- YAMNet이 반환한 영어 소리 라벨에서 사이렌, 경적, 비명 키워드를 찾는다.
- 음성 안내 이벤트와 주변 소리 이벤트가 동시에 감지되면 우선순위가 높은 이벤트를 선택한다.

이벤트 우선순위:

```text
SIREN > SCREAM > HORN > SPEED_WARNING > LEFT_TURN > RIGHT_TURN > NONE
```

우선순위가 필요한 이유:

방향 안내보다 사이렌, 비명, 경적 같은 실제 위험 상황이 더 중요하기 때문이다. 예를 들어 좌회전 안내가 감지되는 중에 사이렌도 감지되면 최종 이벤트는 `SIREN`이 된다.

중요한 함수:

```text
detect_text_event()
- Whisper 텍스트에서 방향 안내와 과속 경고를 찾는다.

detect_sound_event()
- YAMNet 라벨에서 위험 소리를 찾는다.

detect_specific_sound_event()
- 비명처럼 더 민감하게 보고 싶은 이벤트를 따로 검사한다.

merge_events()
- 텍스트 이벤트와 소리 이벤트 중 우선순위가 높은 것을 고른다.
```

## 6. 제출 폴더 구성

최종 제출 폴더에는 아래 파일만 포함한다.

```text
professor_submission/
├─ arduino_smart_handle.ino
├─ raspberry_main.py
├─ pc_ai_server.py
├─ ai_logic.py
└─ CODE_EXPLANATION.md
```
