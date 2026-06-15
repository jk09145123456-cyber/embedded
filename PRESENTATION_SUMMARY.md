# 발표용 핵심 요약

## 프로젝트 주제

시각장애인의 보행 보조를 위한 스마트 핸들 시스템이다.  
주변 소리와 안내 음성을 AI로 분석하고, 결과를 진동 모터와 LED로 전달한다.

## 전체 동작 흐름

```text
마이크 입력
-> Raspberry Pi
-> PC AI 서버
-> AI 분석 결과 반환
-> Arduino
-> 진동 모터 / LED 출력
```

## 파일별 역할

```text
raspberry_main.py
- 마이크 녹음
- PC AI 서버로 오디오 전송
- Arduino로 최종 이벤트 전송

pc_ai_server.py
- Raspberry Pi가 보낸 오디오 수신
- Whisper, YAMNet 모델 실행
- 최종 이벤트를 JSON으로 반환

ai_logic.py
- AI 결과를 실제 이벤트로 변환
- 이벤트 우선순위 처리

arduino_smart_handle.ino
- Serial 명령 수신
- 진동 모터와 LED 제어
```

## 사용한 AI 모델

```text
Whisper
- 한국어 안내 음성을 텍스트로 변환
- 좌회전, 우회전, 과속 경고 판단

YAMNet
- 주변 소리 분류
- 사이렌, 경적, 비명 감지
```

## 이벤트 우선순위

```text
SIREN > SCREAM > HORN > SPEED_WARNING > LEFT_TURN > RIGHT_TURN > NONE
```

방향 안내보다 실제 위험 상황을 먼저 알려야 하므로 사이렌, 비명, 경적에 더 높은 우선순위를 두었다.

## 출력 방식

```text
좌회전: 왼쪽 진동 모터
우회전: 오른쪽 진동 모터
사이렌: 파란 LED + 긴 진동
경적: 노란 LED + 짧은 반복 진동
비명: 빨간 LED + 빠른 반복 진동
과속 경고: 주황 LED + 느린 반복 진동
```

## 구현 포인트

- Raspberry Pi는 녹음과 Arduino 통신만 담당한다.
- 무거운 AI 분석은 PC 서버에서 처리한다.
- Arduino는 `delay()` 대신 `millis()`를 사용해 새 명령에 빠르게 반응한다.
- 같은 이벤트가 반복 전송되지 않도록 Raspberry Pi에서 중복 전송을 막았다.
- 위험 이벤트가 짧게 감지되어도 몇 초간 유지되도록 서버에서 처리했다.
