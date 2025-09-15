#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_MAX31856.h>

// ── Pins ────────────────────────────────────────────────────────────────
// MAX31856 uses hardware SPI on Mega: SCK=52, MISO=50, MOSI=51
const int CS_PIN    = 49;       // single sensor chip-select
const int VALVE_PIN = 7;        // valve control

// ── Control parameters ──────────────────────────────────────────────────
const float SETPOINT   = 25.0;           // °C
const float HYSTERESIS = 0.5;             // °C
const unsigned long MIN_VALVE_INTERVAL = 15000; // ms (15 s)

// ── MAX31856 object ─────────────────────────────────────────────────────
Adafruit_MAX31856 tc(CS_PIN);

unsigned long lastSample = 0;
unsigned long lastValveChange = 0;

enum ValveState { CLOSED = 0, OPEN = 1 };
ValveState valve = CLOSED;

void setup() {
  Serial.begin(115200);

  pinMode(VALVE_PIN, OUTPUT);
  digitalWrite(VALVE_PIN, LOW);

  if (!tc.begin()) {
    Serial.println("MAX31856 begin() failed. Check wiring.");
  }
  tc.setThermocoupleType(MAX31856_TCTYPE_K);       // set your type
  tc.setNoiseFilter(MAX31856_NOISE_FILTER_50HZ);   // or _60HZ

  // CSV header
  Serial.println("time_s,temp_C,valve");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < 1000) return;   // 1 Hz
  lastSample = now;

  // ── 1) Read with basic fault handling ─────────────────────────────────
  float tempC = NAN;
  uint8_t fault = tc.readFault();
  if (fault) {
    Serial.print("Fault 0x");
    Serial.println(fault, HEX);
    // No clearFault() needed, reading already resets latched faults
  } else {
    tempC = tc.readThermocoupleTemperature();
  }

  // ── 2) Hysteresis control with minimum hold time ──────────────────────
  if (!isnan(tempC) && (now - lastValveChange >= MIN_VALVE_INTERVAL)) {
    if (valve == CLOSED && tempC > SETPOINT + HYSTERESIS) {
      valve = OPEN;
      digitalWrite(VALVE_PIN, HIGH);
      lastValveChange = now;
    } else if (valve == OPEN && tempC < SETPOINT - HYSTERESIS) {
      valve = CLOSED;
      digitalWrite(VALVE_PIN, LOW);
      lastValveChange = now;
    }
  }

  // ── 3) Print CSV: time, temp, valve ───────────────────────────────────
  Serial.print(now / 1000.0f, 3);
  Serial.print(',');
  Serial.print(tempC, 2);
  Serial.print(',');
  Serial.println(valve);
}
