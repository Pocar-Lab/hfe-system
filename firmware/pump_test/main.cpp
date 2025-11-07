#include <Arduino.h>

// Mega: D6 -> VFD [12], GND -> [11]. Keep RUN/STOP on keypad (F02=0).
constexpr uint8_t PWM_PIN = 6;

// Adjust to match the drive's max frequency (F03 when using 0-10 V input).
constexpr float MAX_FREQ_HZ       = 60.0f;
constexpr float TARGET_FREQ_HZ    = 40.0f;
constexpr unsigned long RAMP_TIME_MS = 20000UL;  // 20 s ramp up/down
constexpr unsigned long HOLD_TIME_MS = 30000UL;  // 30 s hold at 20 Hz

static uint8_t freqToPwm(float freqHz) {
  float ratio = freqHz / MAX_FREQ_HZ;
  if (ratio < 0.0f) ratio = 0.0f;
  if (ratio > 1.0f) ratio = 1.0f;
  return static_cast<uint8_t>(ratio * 255.0f + 0.5f);
}



static void commandFrequency(float freqHz) {
  analogWrite(PWM_PIN, freqToPwm(freqHz));
}

static void rampPwm(uint8_t startPwm, uint8_t endPwm, unsigned long durationMs) {
  if (startPwm == endPwm) {
    analogWrite(PWM_PIN, endPwm);
    if (durationMs > 0UL) delay(durationMs);
    return;
  }

  const int span = static_cast<int>(endPwm) - static_cast<int>(startPwm);
  unsigned int steps = static_cast<unsigned int>(abs(span));
  if (steps == 0U) {
    analogWrite(PWM_PIN, endPwm);
    if (durationMs > 0UL) delay(durationMs);
    return;
  }

  unsigned long stepDelay = durationMs / steps;
  if (stepDelay == 0UL) stepDelay = 1UL;

  unsigned long elapsed = 0UL;
  int value = static_cast<int>(startPwm);
  const int direction = (span > 0) ? 1 : -1;

  for (unsigned int i = 0; i < steps; ++i) {
    value += direction;
    if (value < 0) value = 0;
    if (value > 255) value = 255;
    analogWrite(PWM_PIN, static_cast<uint8_t>(value));
    delay(stepDelay);
    elapsed += stepDelay;
  }

  analogWrite(PWM_PIN, endPwm);
  if (elapsed < durationMs) delay(durationMs - elapsed);
}

void setup() {
  pinMode(PWM_PIN, OUTPUT);
  commandFrequency(0.0f);
}

void loop() {
  const uint8_t targetPwm = freqToPwm(TARGET_FREQ_HZ);

  rampPwm(0U, targetPwm, RAMP_TIME_MS);
  analogWrite(PWM_PIN, targetPwm);
  delay(HOLD_TIME_MS);
  rampPwm(targetPwm, 0U, RAMP_TIME_MS);

  commandFrequency(0.0f);
  while (true) { delay(1000); }
}
