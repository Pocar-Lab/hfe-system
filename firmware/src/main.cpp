#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MAX31856.h>
#include <math.h>

// ── Shared software-SPI pins ─────────────────────────────────────────────
constexpr int SCK_PIN  = 8;   // CLK
constexpr int MOSI_PIN = 2;   // DI  (MCU -> MAX31856)
constexpr int MISO_PIN = 22;  // DO  (MAX31856 -> MCU)

// ── CS pins for U0..U9 (U9 on D48) ───────────────────────────────────────
constexpr uint8_t CS_PINS[] = { 9, 3, 23, 31, 39, 47, 30, 38, 46, 48 };
constexpr size_t  NUM_TCS   = sizeof(CS_PINS) / sizeof(CS_PINS[0]);

// ── Always emit 10 columns: temp0_C .. temp9_C ───────────────────────────
constexpr size_t MAX_TCS_OUT = 10;

// ── Valve output ─────────────────────────────────────────────────────────
constexpr int VALVE_PIN = 7;

// ── Control parameters ───────────────────────────────────────────────────
constexpr float SETPOINT   = 25.0f;  // °C
constexpr float HYSTERESIS = 0.5f;   // °C

// ── Valve/override state ─────────────────────────────────────────────────
enum ValveState   : uint8_t { CLOSED = 0, OPEN = 1 };
enum OverrideMode : uint8_t { AUTO = 0, FORCE_OPEN = 1, FORCE_CLOSE = 2 };

static ValveState   g_valve = CLOSED;
static OverrideMode g_mode  = AUTO;

// ── Sensor objects (software SPI: (CS, DI, DO, CLK)) ─────────────────────
static Adafruit_MAX31856* tc[NUM_TCS] = { nullptr };

// ── Timing ───────────────────────────────────────────────────────────────
static unsigned long lastSample = 0;

// ── Helpers ──────────────────────────────────────────────────────────────
static void applyValve(ValveState v) {
  g_valve = v;
  digitalWrite(VALVE_PIN, v == OPEN ? HIGH : LOW);
}

static void handleCommand(const String& s) {
  String cmd = s; cmd.trim(); cmd.toUpperCase();
  if (cmd == "VALVE OPEN")       { g_mode = FORCE_OPEN;  applyValve(OPEN);   }
  else if (cmd == "VALVE CLOSE") { g_mode = FORCE_CLOSE; applyValve(CLOSED); }
  else if (cmd == "VALVE AUTO")  { g_mode = AUTO; }
}

// Returns NAN if faulted/missing; otherwise °C
static float safeReadCelsius(Adafruit_MAX31856* dev) {
  if (!dev) return NAN;
  float t = dev->readThermocoupleTemperature();
  uint8_t f = dev->readFault();
  if (f) return NAN; // OPEN/other faults
  if (!isfinite(t) || t < -200.0f || t > 1370.0f) return NAN; // sanity
  return t;
}

void setup() {
  Serial.begin(115200);

  pinMode(VALVE_PIN, OUTPUT);
  applyValve(CLOSED);

  pinMode(SCK_PIN,  OUTPUT);
  pinMode(MOSI_PIN, OUTPUT);
  pinMode(MISO_PIN, INPUT);

  for (size_t i = 0; i < NUM_TCS; ++i) {
    pinMode(CS_PINS[i], OUTPUT);
    digitalWrite(CS_PINS[i], HIGH); // deselect
    tc[i] = new Adafruit_MAX31856(CS_PINS[i], MOSI_PIN, MISO_PIN, SCK_PIN);
    tc[i]->begin();
    tc[i]->setThermocoupleType(MAX31856_TCTYPE_K);
    tc[i]->setNoiseFilter(MAX31856_NOISE_FILTER_60HZ); // correct enum
  }

  // CSV header: time, temp0_C..temp9_C, valve, mode
  Serial.print("time_s");
  for (size_t i = 0; i < MAX_TCS_OUT; ++i) {
    Serial.print(",temp"); Serial.print(i); Serial.print("_C");
  }
  Serial.println(",valve,mode");
}

void loop() {
  // ── Serial command parser (non-blocking) ───────────────────────────────
  static String line;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (line.length()) handleCommand(line); line = ""; }
    else { line += c; if (line.length() > 64) line = ""; }
  }

  // ── 1 Hz sampling ──────────────────────────────────────────────────────
  unsigned long now = millis();
  if (now - lastSample < 1000UL) return;
  lastSample = now;

  // Read sensors into a fixed-size array
  float temps_out[MAX_TCS_OUT];
  for (size_t i = 0; i < MAX_TCS_OUT; ++i) {
    temps_out[i] = (i < NUM_TCS) ? safeReadCelsius(tc[i]) : NAN;
  }

  // Control: average valid of wired ones only
  if (g_mode == AUTO) {
    int k = 0; double sum = 0.0;
    for (size_t i = 0; i < NUM_TCS; ++i) if (isfinite(temps_out[i])) { sum += temps_out[i]; ++k; }
    if (k > 0) {
      float t_ctrl = (float)(sum / k);
      if (g_valve == CLOSED && t_ctrl > SETPOINT + HYSTERESIS) applyValve(OPEN);
      else if (g_valve == OPEN && t_ctrl < SETPOINT - HYSTERESIS) applyValve(CLOSED);
    } else {
      applyValve(CLOSED); // fail-safe
    }
  } else if (g_mode == FORCE_OPEN)  applyValve(OPEN);
  else if (g_mode == FORCE_CLOSE)   applyValve(CLOSED);

  // Stream CSV
  const char modeChar = (g_mode == AUTO) ? 'A' : (g_mode == FORCE_OPEN ? 'O' : 'C');
  const float t_s = now / 1000.0f;

  Serial.print(t_s, 3);
  for (size_t i = 0; i < MAX_TCS_OUT; ++i) {
    Serial.print(',');
    if (isfinite(temps_out[i])) Serial.print(temps_out[i], 2);
    else                        Serial.print("nan");
  }
  Serial.print(',');
  Serial.print((int)g_valve);
  Serial.print(',');
  Serial.println(modeChar);
}
