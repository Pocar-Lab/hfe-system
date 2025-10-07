#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_MAX31856.h>

// ── Pins (Mega2560 hardware SPI: SCK=52, MISO=50, MOSI=51) ─────────────
const int CS1_PIN   = 49;   // Thermocouple #1 CS
const int CS2_PIN   = 48;   // Thermocouple #2 CS
const int VALVE_PIN = 7;    // Valve control (LOW=CLOSED, HIGH=OPEN)

// ── Control parameters ──────────────────────────────────────────────────
const float SETPOINT   = 23.0;                  // °C
const float HYSTERESIS = 0.5;                   // °C
const unsigned long MIN_VALVE_INTERVAL = 15000; // ms (15 s)

// ── MAX31856 instances ──────────────────────────────────────────────────
Adafruit_MAX31856 tc1(CS1_PIN);
Adafruit_MAX31856 tc2(CS2_PIN);

// ── State ───────────────────────────────────────────────────────────────
unsigned long lastSample = 0;
unsigned long lastValveChange = 0;

enum ValveState { CLOSED = 0, OPEN = 1 };
ValveState valve = CLOSED;

// ── Helpers ─────────────────────────────────────────────────────────────
static float read_tc(Adafruit_MAX31856& dev, const char* tag) {
  uint8_t fault = dev.readFault();
  if (fault) {
    Serial.print("fault_"); Serial.print(tag); Serial.print("=0x");
    Serial.println(fault, HEX);
    // Reading clears latched faults; skip this cycle:
    return NAN;
  }
  return dev.readThermocoupleTemperature();
}

static float mean2(float a, float b) {
  bool a_ok = !isnan(a), b_ok = !isnan(b);
  if (a_ok && b_ok) return 0.5f * (a + b);
  if (a_ok) return a;
  if (b_ok) return b;
  return NAN;
}

static void setup_one(Adafruit_MAX31856& dev) {
  if (!dev.begin()) {
    Serial.println("MAX31856 begin() failed (check wiring).");
  }
  dev.setThermocoupleType(MAX31856_TCTYPE_K);     // Set your type here
  dev.setNoiseFilter(MAX31856_NOISE_FILTER_50HZ); // Or MAX31856_NOISE_FILTER_60HZ
}

void setup() {
  Serial.begin(115200);

  pinMode(VALVE_PIN, OUTPUT);
  digitalWrite(VALVE_PIN, LOW);

  setup_one(tc1);
  setup_one(tc2);

  // CSV header for two channels (Python expects this)
  Serial.println("time_s,temp1_C,temp2_C,valve");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < 1000) return;  // ~1 Hz
  lastSample = now;

  // 1) Read sensors
  float t1 = read_tc(tc1, "tc1");
  float t2 = read_tc(tc2, "tc2");
  float t_avg = mean2(t1, t2);

  // 2) Hysteresis control on AVERAGE with minimum hold time
  if (!isnan(t_avg) && (now - lastValveChange >= MIN_VALVE_INTERVAL)) {
    if (valve == CLOSED && t_avg > SETPOINT + HYSTERESIS) {
      valve = OPEN;
      digitalWrite(VALVE_PIN, HIGH);
      lastValveChange = now;
    } else if (valve == OPEN && t_avg < SETPOINT - HYSTERESIS) {
      valve = CLOSED;
      digitalWrite(VALVE_PIN, LOW);
      lastValveChange = now;
    }
  }

  // 3) CSV output: time, t1, t2, valve
  Serial.print(now / 1000.0f, 3); Serial.print(',');
  Serial.print(t1, 2);             Serial.print(',');
  Serial.print(t2, 2);             Serial.print(',');
  Serial.println(valve);
}
