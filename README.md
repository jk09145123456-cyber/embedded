# Smart Handle

음성 안내와 주변 위험 소리를 인식해 **진동과 LED로 운전자에게 알려 주는 스마트 핸들** 임베디드 시스템입니다.
Raspberry Pi가 마이크 오디오를 수집하고, PC AI 서버가 Whisper와 YAMNet으로 분석하며, Arduino가 핸들의 진동 모터와 NeoPixel LED를 제어합니다.

## 목차

- [주요 기능](#주요-기능)
- [시스템 구조](#시스템-구조)
- [디렉토리 구조](#디렉토리-구조)
- [하드웨어 구성](#하드웨어-구성)
- [시작하기](#시작하기)
- [사용법](#사용법)
- [이벤트와 알림 패턴](#이벤트와-알림-패턴)
- [API](#api)
- [기술 스택](#기술-스택)

## 주요 기능

- **음성 안내 인식**: Whisper(faster-whisper)로 한국어 안내 음성을 텍스트로 바꾸고 좌회전, 우회전, 과속 경고를 감지합니다.
- **위험 소리 감지**: YAMNet으로 사이렌, 경적, 비명을 분류합니다.
- **우선순위 기반 판단**: 여러 이벤트가 동시에 감지되면 더 위험한 이벤트를 우선합니다.
- **촉각/시각 피드백**: 좌우 진동 모터와 16구 NeoPixel 링으로 이벤트별 패턴을 출력합니다.
- **논블로킹 제어**: Arduino는 `delay()` 없이 `millis()` 기반으로 동작해 높은 우선순위 알림이 즉시 끼어들 수 있습니다.

## 시스템 구조

```text
┌──────────────┐   HTTP POST /analyze    ┌──────────────────────┐
│ Raspberry Pi │ ──────────────────────▶ │     PC AI Server     │
│  마이크 녹음  │   float32 PCM, 16 kHz   │  Whisper  +  YAMNet  │
│              │ ◀────────────────────── │  우선순위 병합        │
└──────┬───────┘      JSON (event)       └──────────────────────┘
       │ USB Serial (115200 baud)
       ▼
┌──────────────┐
│   Arduino    │  진동 모터 x2, NeoPixel LED 링
└──────────────┘
```

1. Raspberry Pi가 마이크에서 1초 단위로 오디오를 녹음하고 16 kHz로 리샘플링합니다.
2. 오디오를 PC AI 서버의 `/analyze` 엔드포인트로 전송합니다.
3. 서버는 YAMNet으로 주변 소리를 분류하고, 사람 음성이 감지되면 Whisper로 받아쓰기합니다.
4. 텍스트 이벤트와 소리 이벤트 중 우선순위가 높은 것을 최종 이벤트로 반환합니다.
5. Raspberry Pi가 최종 이벤트 이름을 Arduino로 Serial 전송하고, Arduino가 알림 패턴을 출력합니다.

## 디렉토리 구조

```text
.
├── arduino/
│   └── smart_handle/
│       └── smart_handle.ino     # 진동 모터, NeoPixel 제어 펌웨어
├── common/
│   └── protocol.py              # 이벤트 정의와 샘플레이트 (Pi와 서버가 공유)
├── raspberry_pi/
│   ├── raspberry_main.py        # 녹음, 서버 통신, Serial 전송 클라이언트
│   └── requirements.txt
├── server/
│   ├── pc_ai_server.py          # HTTP AI 분석 서버
│   ├── ai_logic.py              # Whisper/YAMNet 호출과 이벤트 판단 로직
│   └── requirements.txt
└── README.md
```

## 하드웨어 구성

| 구성 요소 | 설명 |
| --- | --- |
| Raspberry Pi 4 | USB 마이크 입력, 서버 통신, Arduino 연결 |
| USB 마이크 | 안내 음성과 주변 소리 수집 |
| Arduino (Uno 등) | 진동 모터와 LED 제어 |
| 진동 모터 x2 | 왼쪽 D4, 오른쪽 D2 |
| NeoPixel 링 (16 LED) | 데이터 핀 D6 |
| PC (CUDA GPU 권장) | AI 분석 서버 실행 |

## 시작하기

### 요구 사항

- Python 3.10 이상
- PC 서버: NVIDIA GPU와 CUDA 환경 권장 (CPU 실행도 가능)
- Arduino IDE 및 [Adafruit NeoPixel](https://github.com/adafruit/Adafruit_NeoPixel) 라이브러리

### 설치

```bash
git clone https://github.com/jk09145123456-cyber/embedded.git
cd embedded
```

**PC AI 서버**

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt
```

**Raspberry Pi**

```bash
sudo apt install libportaudio2
python -m venv .venv
source .venv/bin/activate
pip install -r raspberry_pi/requirements.txt
```

**Arduino**

1. Arduino IDE에서 `arduino/smart_handle/smart_handle.ino`를 엽니다.
2. 라이브러리 매니저에서 `Adafruit NeoPixel`을 설치합니다.
3. 보드와 포트를 선택한 뒤 업로드합니다.

## 사용법

모든 Python 명령은 **저장소 루트에서** 모듈 형태로 실행합니다.

### 1. PC AI 서버 실행

```bash
python -m server.pc_ai_server
```

CUDA가 없는 환경에서는 CPU 옵션을 지정합니다.

```bash
python -m server.pc_ai_server --whisper-device cpu --whisper-model small
```

상태 확인:

```bash
curl http://localhost:8000/health
```

### 2. Raspberry Pi 클라이언트 실행

```bash
python -m raspberry_pi.raspberry_main --server-url http://<PC_IP>:8000 --serial-port /dev/ttyACM0
```

장치 없이 동작을 확인하려면 다음 옵션을 사용합니다.

```bash
python -m raspberry_pi.raspberry_main --self-test           # 데이터 형식 자체 점검
python -m raspberry_pi.raspberry_main --no-serial           # Arduino 없이 로그만 출력
python -m raspberry_pi.raspberry_main --debug-wav debug.wav # 녹음된 오디오 저장
```

### 인증 (선택)

서버와 클라이언트에 같은 API 키를 설정하면 `X-API-Key` 헤더로 요청을 인증합니다.

```bash
export SMART_HANDLE_API_KEY=your-secret-key
```

### 주요 옵션

| 대상 | 옵션 | 기본값 | 설명 |
| --- | --- | --- | --- |
| 서버 | `--host` / `--port` | `0.0.0.0` / `8000` | 바인딩 주소 |
| 서버 | `--whisper-model` | `medium` | Whisper 모델 크기 |
| 서버 | `--whisper-device` | `cuda` | `cpu` 또는 `cuda` |
| 서버 | `--sound-threshold` | `0.10` | 위험 소리 판정 임계값 |
| 서버 | `--hazard-hold-seconds` | `5.0` | 위험 이벤트 유지 시간 |
| 클라이언트 | `--seconds` | `1.0` | 1회 녹음 길이 |
| 클라이언트 | `--device` | 기본 입력 장치 | 마이크 번호 또는 이름 |
| 클라이언트 | `--server-url` | `http://127.0.0.1:8000` | AI 서버 주소 |
| 클라이언트 | `--serial-port` | `/dev/ttyACM0` | Arduino 포트 |
| 클라이언트 | `--cooldown` | `2.0` | 같은 이벤트 재전송 간격 |

전체 옵션은 `--help`로 확인할 수 있습니다.

## 이벤트와 알림 패턴

우선순위가 높을수록 더 위험한 이벤트이며, 낮은 우선순위 알림이 진행 중이어도 높은 우선순위 알림이 끼어듭니다.

| 이벤트 | 감지 방법 | 우선순위 | LED | 진동 |
| --- | --- | --- | --- | --- |
| `SCREAM` | YAMNet (scream, shout, yell) | 6 | 빨강 | 양쪽 빠른 반복 |
| `SIREN` | YAMNet (siren, emergency vehicle) | 5 | 파랑 | 양쪽 긴 진동 |
| `HORN` | YAMNet (car horn, honk) | 4 | 노랑 | 양쪽 짧은 반복 |
| `SPEED_WARNING` | Whisper (과속, 속도위반, 제한속도) | 3 | 주황 | 양쪽 느린 반복 |
| `LEFT_TURN` | Whisper (좌회전, 왼쪽, 좌측) | 2 | 없음 | 왼쪽 모터 5초 |
| `RIGHT_TURN` | Whisper (우회전, 오른쪽, 우측) | 1 | 없음 | 오른쪽 모터 5초 |
| `NONE` | 감지 없음 | 0 | 없음 | 없음 |

## API

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /analyze`

| 헤더 | 값 |
| --- | --- |
| `Content-Type` | `application/octet-stream` |
| `X-Sample-Rate` | `16000` (필수) |
| `X-API-Key` | API 키 (설정한 경우) |

요청 본문은 16 kHz mono little-endian float32 PCM이며 최대 30초입니다.

응답 예시:

```json
{
  "event": "SIREN",
  "text": "",
  "stt_event": "NONE",
  "sound_event": "SIREN",
  "sound_label": "Siren",
  "sound_score": 0.42,
  "speech_score": 0.01,
  "voice_rms": 0.003,
  "top_predictions": [["Siren", 0.42], ["Vehicle", 0.12]]
}
```

## 기술 스택

- **Raspberry Pi**: Python, sounddevice, SciPy, pySerial
- **AI 서버**: Python, faster-whisper, TensorFlow Hub (YAMNet), `http.server`
- **Arduino**: C++, Adafruit NeoPixel
