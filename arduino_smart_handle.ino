#include <Adafruit_NeoPixel.h>

/*
  스마트 핸들 Arduino 코드

  역할:
  1. Raspberry Pi에서 USB Serial로 전달한 이벤트 명령을 읽는다.
  2. 이벤트 종류에 따라 진동 모터와 NeoPixel LED를 제어한다.
  3. delay()를 사용하지 않고 millis() 기준으로 시간을 계산한다.

  delay()를 쓰지 않는 이유:
  - delay()가 실행되는 동안 Arduino가 새 Serial 명령을 바로 읽지 못한다.
  - 이 프로젝트는 사이렌/비명 같은 위험 이벤트가 들어오면 빠르게 반응해야 한다.
  - 그래서 loop()가 계속 돌면서 명령 수신과 패턴 갱신을 동시에 처리하도록 작성했다.
*/

// 스마트 핸들에 연결된 출력 핀 번호를 한 곳에서 관리한다.
// 실제 배선 기준으로 왼쪽/오른쪽 진동 모터 핀을 지정한다.
const uint8_t LEFT_MOTOR_PIN = 4;
const uint8_t RIGHT_MOTOR_PIN = 2;

// LED는 Adafruit NeoPixel 라이브러리로 제어한다.
// 수업에서 배운 기본 LED ON/OFF와 원리는 같지만,
// 이 LED 링은 색상과 여러 개의 LED를 다루기 위해 전용 라이브러리가 필요하다.
const uint8_t LED_PIN = 6;
const uint16_t LED_COUNT = 16;

// Raspberry Pi와 시리얼 통신할 때 사용하는 속도이다.
// Raspberry Pi 코드의 기본 baudrate도 115200으로 맞춰져 있어야 한다.
const unsigned long SERIAL_BAUDRATE = 115200;

// 각 상황별 LED/진동 유지 시간이다.
// 단위는 모두 밀리초(ms)이다.
const unsigned long TURN_VIBRATION_MS = 5000;
const unsigned long ALERT_LED_HOLD_MS = 10000;
const unsigned long ALERT_VIBRATION_MS = 8000;

// 새 명령이 너무 빠르게 들어와도 최소 5초는 현재 알림을 유지한다.
// 사용자가 알림을 인지하기 전에 바로 꺼지는 것을 방지하기 위한 값이다.
const unsigned long MIN_PATTERN_HOLD_MS = 5000;

// 양쪽 모터를 모두 써야 하는 이벤트에서는 이 시간마다 왼쪽/오른쪽을 번갈아 켠다.
const unsigned long MOTOR_ALTERNATE_MS = 250;

// LED 밝기는 너무 눈부시지 않게 30으로 제한한다.
const uint8_t LED_BRIGHTNESS = 30;

// LED 링 객체를 생성한다.
Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

// 하나의 알림 패턴에 필요한 LED 색상, 진동 시간, 반복 횟수 등을 묶은 구조체이다.
struct FeedbackPattern {
  // LED 색상값이다. 각 값은 0~255 범위이다.
  uint8_t red;
  uint8_t green;
  uint8_t blue;

  // 모터가 켜져 있는 시간과 꺼져 있는 시간이다.
  unsigned long onMs;
  unsigned long offMs;

  // 진동 반복 횟수이다. 예: on 500ms/off 500ms/repetitions 8이면 약 8초 반복한다.
  uint8_t repetitions;

  // 어떤 모터를 사용할지 나타내는 비트값이다.
  // 0x01 왼쪽, 0x02 오른쪽, 0x03 양쪽이다.
  uint8_t motorMask;

  // 진동이 끝난 뒤에도 LED를 유지해야 하는 전체 시간이다.
  unsigned long ledHoldMs;
};

// 현재 실행 중인 알림 패턴의 상태값이다.
FeedbackPattern activePattern = {0, 0, 0, 0, 0, 0, 0, 0};

// millis() 기준으로 현재 패턴이 시작된 시간을 저장한다.
unsigned long patternStartedAt = 0;

// 현재 LED/진동 패턴이 실행 중인지 표시한다.
bool patternActive = false;

// 현재 실행 중인 명령 문자열이다. 같은 명령이 반복될 때 다시 시작하지 않기 위해 저장한다.
String activeCommand = "";

// 현재 명령의 위험도 우선순위이다.
uint8_t activePriority = 0;

// LED 링 전체를 같은 색으로 켠다.
void setLed(uint8_t red, uint8_t green, uint8_t blue) {
  // strip.Color()가 RGB 값을 NeoPixel이 이해하는 색상값으로 바꾼다.
  strip.fill(strip.Color(red, green, blue));

  // show()를 호출해야 실제 LED 링에 색상 변경이 반영된다.
  strip.show();
}

// motorMask 값으로 왼쪽/오른쪽 진동 모터를 켜고 끈다.
// 0x01은 왼쪽, 0x02는 오른쪽, 0x03은 양쪽을 의미한다.
void setMotors(uint8_t mask) {
  // 비트 연산을 사용하면 하나의 값(mask)으로 두 모터 상태를 동시에 표현할 수 있다.
  digitalWrite(LEFT_MOTOR_PIN, (mask & 0x01) ? HIGH : LOW);
  digitalWrite(RIGHT_MOTOR_PIN, (mask & 0x02) ? HIGH : LOW);
}

// 양쪽 모터를 동시에 계속 켜면 전류가 순간적으로 커질 수 있어 번갈아 켠다.
void setPatternMotors(uint8_t mask, unsigned long elapsed) {
  if (mask == 0x03) {
    // elapsed / MOTOR_ALTERNATE_MS의 짝수/홀수 여부로 왼쪽과 오른쪽을 번갈아 선택한다.
    setMotors(((elapsed / MOTOR_ALTERNATE_MS) % 2 == 0) ? 0x01 : 0x02);
  } else {
    setMotors(mask);
  }
}

// 모든 출력을 끄고 현재 알림 상태를 초기화한다.
void stopOutputs() {
  setMotors(0);
  setLed(0, 0, 0);
  patternActive = false;
  activeCommand = "";
  activePriority = 0;
}

// 위험도가 높은 이벤트가 낮은 이벤트보다 우선 실행되도록 점수를 정한다.
uint8_t commandPriority(const String &command) {
  // 사이렌은 보행자에게 가장 즉각적인 위험이 될 수 있어 가장 높은 우선순위를 둔다.
  if (command == "SIREN") return 6;
  if (command == "SCREAM") return 5;
  if (command == "HORN") return 4;
  if (command == "SPEED_WARNING") return 3;
  if (command == "LEFT_TURN" || command == "RIGHT_TURN") return 1;
  return 0;
}

// 새 알림 패턴을 시작한다.
void startPattern(const String &command, const FeedbackPattern &pattern) {
  // 새 패턴의 명령명과 우선순위를 저장한다.
  activeCommand = command;
  activePriority = commandPriority(command);

  // 전달받은 패턴을 현재 실행할 패턴으로 복사한다.
  activePattern = pattern;

  // 시작 시간을 저장해야 updatePattern()에서 경과 시간을 계산할 수 있다.
  patternStartedAt = millis();
  patternActive = true;

  // 패턴 시작과 동시에 LED와 모터를 바로 켠다.
  setLed(pattern.red, pattern.green, pattern.blue);
  setPatternMotors(pattern.motorMask, 0);
}

// loop()에서 계속 호출되며, 시간이 지남에 따라 진동과 LED를 갱신한다.
void updatePattern() {
  if (!patternActive) {
    return;
  }

  const unsigned long elapsed = millis() - patternStartedAt;

  // 한 번의 진동 주기 = 켜짐 시간 + 꺼짐 시간이다.
  const unsigned long cycleMs = activePattern.onMs + activePattern.offMs;

  // 전체 진동이 끝나는 시점을 계산한다.
  // 마지막 반복 뒤에는 off 시간이 필요 없으므로 repetitions - 1만큼만 offMs를 더한다.
  const unsigned long vibrationEndMs =
      activePattern.repetitions == 0 ? 0 :
      activePattern.repetitions * activePattern.onMs +
      (activePattern.repetitions - 1) * activePattern.offMs;

  // 반복 패턴 시간 안에서는 on/off 주기에 맞춰 모터를 제어한다.
  if (elapsed < vibrationEndMs && cycleMs > 0) {
    if ((elapsed % cycleMs) < activePattern.onMs) {
      setPatternMotors(activePattern.motorMask, elapsed);
    } else {
      setMotors(0);
    }
  } else {
    setMotors(0);
  }

  // 최소 표시 시간이 끝나고 LED/진동 시간이 모두 끝나면 전체 출력을 끈다.
  if (elapsed >= MIN_PATTERN_HOLD_MS &&
      elapsed >= activePattern.ledHoldMs &&
      elapsed >= vibrationEndMs) {
    stopOutputs();
  }
}

// Raspberry Pi에서 받은 명령 문자열을 실제 LED/진동 패턴으로 변환한다.
void handleCommand(String command) {
  // Raspberry Pi는 명령 뒤에 줄바꿈을 붙여 보내므로 앞뒤 공백과 줄바꿈을 제거한다.
  command.trim();

  // 같은 명령이 반복해서 들어오면 패턴을 다시 시작하지 않는다.
  if (patternActive && command == activeCommand) {
    return;
  }

  // 알림이 너무 짧게 끊기지 않도록 최소 유지 시간 동안은 새 명령을 무시한다.
  if (patternActive && millis() - patternStartedAt < MIN_PATTERN_HOLD_MS) {
    return;
  }

  // 사이렌, 비명, 경적 같은 위험 알림은 방향 안내보다 우선한다.
  if (patternActive && activePriority >= 3 && commandPriority(command) < activePriority) {
    return;
  }

  if (command == "LEFT_TURN") {
    // 방향 안내는 LED 없이 해당 방향 모터만 켜서 사용자가 방향을 느끼도록 한다.
    startPattern(command, {0, 0, 0, TURN_VIBRATION_MS, 0, 1, 0x01, TURN_VIBRATION_MS});
  } else if (command == "RIGHT_TURN") {
    startPattern(command, {0, 0, 0, TURN_VIBRATION_MS, 0, 1, 0x02, TURN_VIBRATION_MS});
  } else if (command == "SIREN") {
    // 사이렌: 파란 LED와 긴 진동으로 긴급 차량 접근을 알린다.
    startPattern(command, {0, 0, 255, ALERT_VIBRATION_MS, 0, 1, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "HORN") {
    // 경적: 노란 LED와 짧은 반복 진동을 사용한다.
    startPattern(command, {255, 180, 0, 500, 500, 8, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "SCREAM") {
    // 비명: 빨간 LED와 빠른 반복 진동으로 위험 상황을 강조한다.
    startPattern(command, {255, 0, 0, 200, 150, 23, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "SPEED_WARNING") {
    // 과속 경고: 주황 LED와 느린 반복 진동을 사용한다.
    startPattern(command, {255, 80, 0, 900, 600, 6, 0x03, ALERT_LED_HOLD_MS});
  }
}

// 아두이노가 시작될 때 핀, LED 링, 시리얼 통신을 초기화한다.
void setup() {
  pinMode(LEFT_MOTOR_PIN, OUTPUT);
  pinMode(RIGHT_MOTOR_PIN, OUTPUT);
  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  stopOutputs();
  Serial.begin(SERIAL_BAUDRATE);
  Serial.setTimeout(20);
}

// Raspberry Pi에서 명령을 받으면 처리하고, 현재 알림 패턴을 계속 갱신한다.
void loop() {
  if (Serial.available() > 0) {
    handleCommand(Serial.readStringUntil('\n'));
  }
  updatePattern();
}
