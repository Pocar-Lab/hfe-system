#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MAX31856.h>

// ── Shared software-SPI pins ─────────────────────────────────────────────
constexpr int SCK_PIN  = 8;   // CLK
constexpr int MOSI_PIN = 2;   // DI  (MCU -> MAX31856)
constexpr int MISO_PIN = 22;  // DO  (MAX31856 -> MCU)

// ── Chip-select pins for 9 thermocouples (your list, in order) ──────────
constexpr uint8_t CS_PINS[9] = { 9, 3, 23, 31, 39, 47, 30, 38, 46 };

// ── Valve output ────────────────────────────────────────────────────────
constexpr int VALVE_PIN = 7;

// ── Control parameters ──────────────────────────────────────────────────
constexpr float SETPOINT   = 25.0f;  // °C
constexpr float HYSTERESIS = 0.5f;   // °C

// ── Valve/override state ────────────────────────────────────────────────
enum ValveState   : uint8_t { CLOSED = 0, OPEN = 1 };
enum OverrideMode : uint8_t { AUTO = 0, FORCE_OPEN = 1, FORCE_CLOSE = 2 };

static ValveState   g_valve = CLOSED;
static OverrideMode g_mode  = AUTO;

// ── 9 sensor objects (software SPI: ctor = (CS, DI, DO, CLK)) ───────────
static Adafruit_MAX31856* tc[9] = { nullptr };

// ── Timing ──────────────────────────────────────────────────────────────
static unsigned long lastSample = 0;

// ── Helpers ─────────────────────────────────────────────────────────────
static void applyValve(ValveState v) {
  g_valve = v;
  digitalWrite(VALVE_PIN, v == OPEN ? HIGH : LOW);
}

static void handleCommand(const String& s) {
  String cmd = s; cmd.trim(); cmd.toUpperCase();
  if (cmd == "VALVE OPEN")  { g_mode = FORCE_OPEN;  applyValve(OPEN);   }
  else if (cmd == "VALVE CLOSE") { g_mode = FORCE_CLOSE; applyValve(CLOSED); }
  else if (cmd == "VALVE AUTO")  { g_mode = AUTO; }
}

// Returns NAN if faulted or not present; otherwise °C
static float safeReadCelsius(Adafruit_MAX31856& dev) {
  uint8_t f = dev.readFault();
  if (f) {
    // Common "not connected" is OPEN fault; any nonzero fault -> invalid
    return NAN;
  }
  float t = dev.readThermocoupleTemperature();
  // Additional sanity check
  if (!isfinite(t) || t < -200.0f || t > 1370.0f) return NAN; // K-type range guard
  return t;
}

void setup() {
  Serial.begin(115200);

  // Valve output
  pinMode(VALVE_PIN, OUTPUT);
  applyValve(CLOSED);

  // Shared SPI directions (library will handle as needed; explicit is fine)
  pinMode(SCK_PIN,  OUTPUT);
  pinMode(MOSI_PIN, OUTPUT);
  pinMode(MISO_PIN, INPUT);

  // Instantiate and init all 9 channels
  for (int i = 0; i < 9; ++i) {
    pinMode(CS_PINS[i], OUTPUT);
    digitalWrite(CS_PINS[i], HIGH);        // deselect
    tc[i] = new Adafruit_MAX31856(CS_PINS[i], MOSI_PIN, MISO_PIN, SCK_PIN);
    tc[i]->begin();
    tc[i]->setThermocoupleType(MAX31856_TCTYPE_K);
  }

  // CSV header: time, t0..t8, valve, mode
  Serial.print("time_s");
  for (int i = 0; i < 9; ++i) { Serial.print(",temp"); Serial.print(i); Serial.print("_C"); }
  Serial.println(",valve,mode");
}

void loop() {
  // ── Serial command parser (non-blocking) ──────────────────────────────
  static String line;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (line.length()) handleCommand(line); line = ""; }
    else { line += c; if (line.length() > 64) line = ""; }
  }

  // ── 1 Hz loop ─────────────────────────────────────────────────────────
  unsigned long now = millis();
  if (now - lastSample < 1000) return;
  lastSample = now;

  // 1) Read all sensors
  float temps[9];
  int   validCount = 0;
  for (int i = 0; i < 9; ++i) {
    float t = safeReadCelsius(*tc[i]);
    temps[i] = t;
    if (isfinite(t)) ++validCount;
  }

  // 2) Control (AUTO uses average of valid sensors; no valid -> close valve)
  if (g_mode == AUTO) {
    if (validCount > 0) {
      double sum = 0.0;
      for (int i = 0; i < 9; ++i) if (isfinite(temps[i])) sum += temps[i];
      float t_ctrl = (float)(sum / validCount);
      if (g_valve == CLOSED && t_ctrl > SETPOINT + HYSTERESIS) {
        applyValve(OPEN);
      } else if (g_valve == OPEN && t_ctrl < SETPOINT - HYSTERESIS) {
        applyValve(CLOSED);
      }
    } else {
      // fail-safe: no valid temp, keep closed
      applyValve(CLOSED);
    }
  } else if (g_mode == FORCE_OPEN) {
    applyValve(OPEN);
  } else if (g_mode == FORCE_CLOSE) {
    applyValve(CLOSED);
  }

  // 3) Stream CSV
  const char modeChar = (g_mode == AUTO) ? 'A' : (g_mode == FORCE_OPEN ? 'O' : 'C');
  const float t_s = now / 1000.0f;

  Serial.print(t_s, 3);
  for (int i = 0; i < 9; ++i) {
    Serial.print(',');
    if (isfinite(temps[i])) Serial.print(temps[i], 2);
    else                    Serial.print("nan");
  }
  Serial.print(',');
  Serial.print((int)g_valve);
  Serial.print(',');
  Serial.println(modeChar);
}