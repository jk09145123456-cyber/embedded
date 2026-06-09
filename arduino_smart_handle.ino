#include <Adafruit_NeoPixel.h>

// The physical motors are wired opposite to the original labels.
const uint8_t LEFT_MOTOR_PIN = 4;
const uint8_t RIGHT_MOTOR_PIN = 2;
const uint8_t LED_PIN = 6;
const uint16_t LED_COUNT = 16;
const unsigned long SERIAL_BAUDRATE = 115200;
const unsigned long TURN_VIBRATION_MS = 5000;
const unsigned long ALERT_LED_HOLD_MS = 10000;
const unsigned long ALERT_VIBRATION_MS = 8000;
const unsigned long MIN_PATTERN_HOLD_MS = 5000;
const unsigned long MOTOR_ALTERNATE_MS = 250;
const uint8_t LED_BRIGHTNESS = 30;

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

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

FeedbackPattern activePattern = {0, 0, 0, 0, 0, 0, 0, 0};
unsigned long patternStartedAt = 0;
bool patternActive = false;
String activeCommand = "";
uint8_t activePriority = 0;

void setLed(uint8_t red, uint8_t green, uint8_t blue) {
  strip.fill(strip.Color(red, green, blue));
  strip.show();
}

void setMotors(uint8_t mask) {
  digitalWrite(LEFT_MOTOR_PIN, (mask & 0x01) ? HIGH : LOW);
  digitalWrite(RIGHT_MOTOR_PIN, (mask & 0x02) ? HIGH : LOW);
}

void setPatternMotors(uint8_t mask, unsigned long elapsed) {
  if (mask == 0x03) {
    // Avoid the startup-current spike caused by powering both motors together.
    setMotors(((elapsed / MOTOR_ALTERNATE_MS) % 2 == 0) ? 0x01 : 0x02);
  } else {
    setMotors(mask);
  }
}

void stopOutputs() {
  setMotors(0);
  setLed(0, 0, 0);
  patternActive = false;
  activeCommand = "";
  activePriority = 0;
}

uint8_t commandPriority(const String &command) {
  if (command == "SIREN") return 6;
  if (command == "SCREAM") return 5;
  if (command == "HORN") return 4;
  if (command == "SPEED_WARNING") return 3;
  if (command == "LEFT_TURN" || command == "RIGHT_TURN") return 1;
  return 0;
}

void startPattern(const String &command, const FeedbackPattern &pattern) {
  activeCommand = command;
  activePriority = commandPriority(command);
  activePattern = pattern;
  patternStartedAt = millis();
  patternActive = true;
  setLed(pattern.red, pattern.green, pattern.blue);
  setPatternMotors(pattern.motorMask, 0);
}

void updatePattern() {
  if (!patternActive) {
    return;
  }

  const unsigned long elapsed = millis() - patternStartedAt;
  const unsigned long cycleMs = activePattern.onMs + activePattern.offMs;
  const unsigned long vibrationEndMs =
      activePattern.repetitions == 0 ? 0 :
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

  if (elapsed >= MIN_PATTERN_HOLD_MS &&
      elapsed >= activePattern.ledHoldMs &&
      elapsed >= vibrationEndMs) {
    stopOutputs();
  }
}

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
    // Blue LED with one long continuous vibration.
    startPattern(command, {0, 0, 255, ALERT_VIBRATION_MS, 0, 1, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "HORN") {
    // Yellow LED with short, strong pulses for about 8 seconds.
    startPattern(command, {255, 180, 0, 500, 500, 8, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "SCREAM") {
    // Red LED with fast repeated vibration for about 8 seconds.
    startPattern(command, {255, 0, 0, 200, 150, 23, 0x03, ALERT_LED_HOLD_MS});
  } else if (command == "SPEED_WARNING") {
    // Orange LED with slower repeated vibration for about 8 seconds.
    startPattern(command, {255, 80, 0, 900, 600, 6, 0x03, ALERT_LED_HOLD_MS});
  }
}

void setup() {
  pinMode(LEFT_MOTOR_PIN, OUTPUT);
  pinMode(RIGHT_MOTOR_PIN, OUTPUT);
  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  stopOutputs();
  Serial.begin(SERIAL_BAUDRATE);
  Serial.setTimeout(20);
}

void loop() {
  if (Serial.available() > 0) {
    handleCommand(Serial.readStringUntil('\n'));
  }
  updatePattern();
}
