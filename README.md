# 시각장애인을 위한 스마트 핸들 시스템

Raspberry Pi가 마이크 오디오를 PC의 AI 서버로 전송하고, PC가 Whisper와 YAMNet으로
분석한 이벤트를 반환합니다. Raspberry Pi는 반환된 이벤트를 USB Serial로 Arduino에
전달하며, Arduino가 진동 모터와 LED를 제어합니다.

## 구성

```text
마이크 -> Raspberry Pi -> Wi-Fi/LAN -> PC AI 서버
       <- 이벤트 결과 <-
Raspberry Pi -> USB Serial -> Arduino -> 진동 모터/LED
```

PC와 Raspberry Pi는 실행 중 같은 네트워크에 상시 연결되어 있어야 합니다.

## Arduino 설치

Arduino IDE Library Manager에서 `Adafruit NeoPixel`을 설치한 뒤
`arduino_smart_handle.ino`를 업로드합니다. Arduino와 Raspberry Pi는 USB로 연결합니다.

현재 핀 설정:

```text
왼쪽 진동 모터 제어: D4
오른쪽 진동 모터 제어: D2
WS2812B LED 데이터: D6
WS2812B LED 개수: 16
Serial 속도: 115200
```

## PC AI 서버 설치 및 실행

Python 가상환경을 만들고 AI 전용 패키지를 설치합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-pc.txt
python pc_ai_server.py --host 0.0.0.0 --port 8000
```

최초 실행 시 Whisper와 YAMNet 모델을 인터넷에서 다운로드합니다. 이후 모델 캐시가
유지되면 인터넷 없이도 같은 LAN 안에서 실행할 수 있습니다.

Windows 방화벽에서 TCP `8000` 포트의 인바운드 연결을 허용해야 합니다. PC의 로컬
IP 주소는 `ipconfig`로 확인합니다.

서버 상태 확인:

```text
http://PC_IP주소:8000/health
```

## Raspberry Pi 설치 및 실행

Raspberry Pi에는 AI 모델이나 TensorFlow를 설치하지 않습니다.

```bash
sudo apt update
sudo apt install -y libportaudio2 portaudio19-dev python3-venv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-pi.txt

python raspberry_main.py --self-test
python raspberry_main.py --server-url http://PC_IP주소:8000 --serial-port /dev/ttyACM0
```

현재 Google VoiceHAT 마이크 설정으로 전체 실행:

```bash
python raspberry_main.py \
  --server-url http://PC_IP주소:8000 \
  --device 1 \
  --input-sample-rate 48000 \
  --serial-port /dev/ttyACM0
```

Arduino 없이 네트워크와 AI 분석만 확인:

```bash
python raspberry_main.py --server-url http://PC_IP주소:8000 --no-serial
```

## API 키 설정

공용 네트워크를 사용할 때는 PC와 Raspberry Pi 양쪽에 같은 키를 설정합니다.

PC PowerShell:

```powershell
$env:SMART_HANDLE_API_KEY="변경할-긴-키"
python pc_ai_server.py
```

Raspberry Pi:

```bash
export SMART_HANDLE_API_KEY="변경할-긴-키"
python raspberry_main.py --server-url http://PC_IP주소:8000
```

## 주요 옵션

```text
PC:
--whisper-model small       Whisper 모델 크기
--whisper-device cpu        Whisper 실행 장치: cpu 또는 cuda
--whisper-compute-type int8 CPU는 int8, GPU는 float16 권장
--sound-threshold 0.10      환경음 감지 최소 점수
--scream-threshold 0.08     비명 계열 감지 최소 점수
--speech-window-seconds 2   좌·우회전 음성 누적 분석 길이
--sound-window-seconds 1    외부 위험음 분석 길이
--hazard-hold-seconds 5     감지된 외부 위험 이벤트 유지 시간
--voice-rms-threshold 0.005 녹음·TTS 음성 분석 최소 음량
--speed-reference FILE      속도 경고 딩동 기준 WAV 파일
--speed-match-threshold 0.55 기준음 유사도 임계값
--reference-dir DIR         좌회전·우회전·속도 기준 WAV 폴더
--reference-match-threshold 0.55 기준음 폴더 유사도 임계값
--host 0.0.0.0              접속 허용 주소
--port 8000                 서버 포트

Raspberry Pi:
--server-url URL            PC AI 서버 주소
--server-timeout 60         AI 응답 제한 시간
--seconds 1                 외부 위험음 분석 조각 길이
--device DEVICE             마이크 장치 번호 또는 이름
--input-sample-rate RATE    마이크 녹음 주파수 직접 지정
--serial-port PORT          Arduino Serial 포트
--no-serial                 Arduino 없이 로그만 확인
```

`Invalid sample rate [PaErrorCode -9997]`가 발생하면 `python -m sounddevice`로 입력
장치 번호를 확인한 뒤 `--device 번호`를 지정합니다. 클라이언트는 선택한 장치의 기본
주파수로 녹음하고 AI 서버가 요구하는 16kHz로 자동 변환합니다. 기본 주파수 탐지가
잘못된 장치는 `--input-sample-rate 48000`처럼 직접 지정할 수 있습니다.

## 속도 경고 고정음 감지

좌회전과 우회전은 Whisper가 안내 멘트를 인식합니다. 속도위반 안내는 항상 같은
`딩동 딩동` 소리라면 기준 WAV 파일과 유사도를 비교해 감지할 수 있습니다.

먼저 Pi에서 속도 경고음만 포함되도록 짧게 녹음합니다.

```bash
arecord -D plughw:3,0 -r 48000 -c 1 -f S16_LE speed_warning.wav
```

경고음 재생이 끝나면 `Ctrl+C`를 누르고, `speed_warning.wav`를 PC 프로젝트 폴더로
옮깁니다. PC 서버를 다음처럼 실행합니다.

```powershell
python pc_ai_server.py --speed-reference speed_warning.wav
```

로그의 `speed=0.000` 값을 확인해 임계값을 조정합니다. 실제 경고음 점수가 낮으면
`--speed-match-threshold 0.40`, 일반 소리도 경고로 잡히면 `0.65`처럼 높입니다.

좌회전·우회전 TTS WAV를 여러 개 사용할 때는 파일명에 `좌회전` 또는 `우회전`을
포함하고 폴더를 지정합니다. Whisper가 방향이나 속도 안내를 인식하지 못했을 때만
기준음 결과를 보조 판정으로 사용합니다.

```powershell
python pc_ai_server.py `
  --host 0.0.0.0 `
  --port 8000 `
  --reference-dir "새+프로젝트" `
  --reference-match-threshold 0.55
```

파일명에 `속도` 또는 `과속`이 들어간 WAV를 같은 폴더에 추가하면 속도 경고 기준음도
함께 감지합니다. 빠른 반응을 위해 Pi는 `--seconds 1`을 사용합니다.

Pi가 1초 단위로 소리를 보내면 서버는 사이렌·비명·클락션을 최신 1초에서 먼저
판단합니다. 외부 위험음이 감지되면 Whisper를 기다리지 않고 즉시 반환합니다.
좌회전·우회전 같은 안내 음성은 서버가 최근 3초를 누적해 Whisper로 분석합니다.
Pi는 서버 응답을 기다리는 동안에도 백그라운드에서 계속 녹음합니다. 서버 처리가
밀리면 오래된 녹음은 버리고 가장 최근 녹음을 전송합니다.

## LED 및 진동 패턴

위험 이벤트 LED는 10초간 유지됩니다. Arduino는 `delay()`로 대기하지 않으므로 LED가
켜진 중에도 새 명령을 즉시 받을 수 있습니다.

```text
사이렌: 파란색, 양쪽 연속 진동 8초
클랙슨: 노란색, 짧고 강한 진동 약 8초
비명: 빨간색, 빠른 진동 약 8초
속도 위반: 주황색, 느린 반복 진동 약 8초
좌회전: 왼쪽 모터 연속 진동 2.5초
우회전: 오른쪽 모터 연속 진동 2.5초
```

## 이벤트 우선순위

`SIREN > SCREAM > HORN > SPEED_WARNING > LEFT_TURN > RIGHT_TURN > NONE`
