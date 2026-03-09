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

// ── Heater relays ────────────────────────────────────────────────────────
constexpr int HEATER_BOTTOM_PIN = 11;  // tank bottom heater relay
constexpr int HEATER_EXHAUST_PIN = 5;  // LN exhaust heater relay

// ── Pump / VFD (Fuji FRENIC-Mini) ────────────────────────────────────────
constexpr uint8_t  PWM_PIN           = 6;       // OC4A on Arduino Mega
constexpr uint16_t PWM_TOP           = 999;     // 2 kHz with prescaler 8
constexpr float    PUMP_CMD_MAX_PCT  = 100.0f;  // clamp analog command to 0–100 % of full scale
constexpr float    PUMP_MAX_FREQ_HZ  = 71.7f;   // 100% -> 71.7 Hz (≈2150 rpm, ≈4.0 L/min HFE)
constexpr float    VFD_RATED_CURRENT_A = 2.8f;  // inverter rated current / motor nameplate
constexpr float    VFD_RATED_POWER_W = 400.0f;  // motor rated output power (W) for %→W, update to nameplate
constexpr float    VFD_BASE_VOLTAGE  = 230.0f;  // nominal output voltage for % display
constexpr uint8_t  VFD_SLAVE_ADDR    = 1;       // y01
constexpr uint32_t VFD_BAUD          = 9600;    // y04
constexpr unsigned long VFD_POLL_MS  = 1000UL;  // poll M09–M12 once per second

// ── Flow meter (KROHNE MFC400 via DFR0845 on Serial2) ───────────────────
constexpr uint8_t  FLOW_SLAVE_ADDR   = 1;
constexpr uint32_t FLOW_BAUD         = 19200;
constexpr unsigned long FLOW_POLL_MS = 1000UL;
constexpr uint16_t FLOW_REG_START    = 30000;   // 30000..30008, five floats
constexpr uint8_t  FLOW_REG_COUNT    = 10;      // 10 x 16-bit regs = 5 x float32
constexpr float    FLUID_CONC_PCT    = 100.0f;
constexpr char     FLUID_NAME[]      = "HFE-7200";

// ── Pressure sensors (0–5.013 V = 10 bar gauge) ─────────────────────────
// IMPORTANT: these must be analog-capable pins (A0–A15 on the Mega). If you move the wiring,
// update these constants to the matching Ax (or 54..69) numbers.
constexpr uint8_t PRESSURE_PIN_BEFORE = A8;  // before pump
constexpr uint8_t PRESSURE_PIN_AFTER  = A0;  // after pump
constexpr uint8_t PRESSURE_PIN_TANK   = A1;  // tank
constexpr float   PRESSURE_FSO_V      = 5.013f;   // full-scale output voltage
constexpr float   PRESSURE_FSO_BAR    = 10.0f;    // full-scale in bar (gauge)
constexpr float   PRESSURE_ERR_BAR    = 0.05f;    // sensor accuracy (±)
constexpr float   ADC_REF_V           = 5.0f;     // default analog reference (5 V)
constexpr float   PSI_PER_BAR         = 14.5037738f;
constexpr float   ATMOSPHERE_BAR      = 1.01325f; // add for absolute pressure display
constexpr float   PRESSURE_AFTER_ZERO_V = 0.029f; // 1 atm output for after-pump sensor

// Modbus group M registers (Fuji FRENIC-Mini)
constexpr uint16_t REG_M09 = 0x0809;  // output frequency (0.01 Hz)
constexpr uint8_t  N_M_REG = 4;       // M09–M12 inclusive

// ── Control parameters ───────────────────────────────────────────────────
constexpr float SETPOINT   = 25.0f;  // °C
constexpr float HYSTERESIS = 0.5f;   // °C

// ── Valve/override state ─────────────────────────────────────────────────
enum ValveState   : uint8_t { CLOSED = 0, OPEN = 1 };
enum OverrideMode : uint8_t { AUTO = 0, FORCE_OPEN = 1, FORCE_CLOSE = 2 };

static ValveState   g_valve = CLOSED;
static OverrideMode g_mode  = AUTO;
static bool         g_heater_bottom_on = false;
static bool         g_heater_exhaust_on = false;

// ── Sensor objects (software SPI: (CS, DI, DO, CLK)) ─────────────────────
static Adafruit_MAX31856* tc[NUM_TCS] = { nullptr };

// ── Timing ───────────────────────────────────────────────────────────────
static unsigned long lastSample = 0;
static unsigned long lastVfdPoll = 0;
static unsigned long lastFlowPoll = 0;
constexpr unsigned long SAMPLE_INTERVAL_MS = 1000UL;

// ── Pump / VFD state ─────────────────────────────────────────────────────
HardwareSerial &VFD = Serial3;
HardwareSerial &FLOW = Serial2;

struct VfdSnapshot {
  bool   valid;
  float  freqHz;
  float  inputPowerPct;
  float  outputCurrentPct;
  float  outputVoltageV;
  unsigned long lastPollMs;
};

static VfdSnapshot g_vfd = { false, NAN, NAN, NAN, NAN, 0 };
static float       g_pump_cmd_pct = 0.0f;

struct FlowSnapshot {
  bool   valid;
  float  flowVelocityMps;
  float  volumeFlowM3s;
  float  massFlowKgS;
  float  temperatureRaw;
  float  densityKgM3;
  unsigned long lastPollMs;
};

static FlowSnapshot g_flow = { false, NAN, NAN, NAN, NAN, NAN, 0 };

// ── Helpers ──────────────────────────────────────────────────────────────
static float readPressureVolts(uint8_t pin) {
  int raw = analogRead(pin);
  if (raw < 0 || raw > 1023) return NAN;
  return raw * (ADC_REF_V / 1023.0f);
}

static float voltsToBar(float volts) {
  if (!isfinite(volts)) return NAN;
  float bar = volts * (PRESSURE_FSO_BAR / PRESSURE_FSO_V);
  if (!isfinite(bar)) return NAN;
  if (bar < 0.02f) bar = 0.0f; // clamp small offsets/noise
  return bar;
}

static float voltsToBarAfter(float volts) {
  if (!isfinite(volts)) return NAN;
  const float slope = PRESSURE_FSO_BAR / (PRESSURE_FSO_V - PRESSURE_AFTER_ZERO_V);
  float bar = (volts - PRESSURE_AFTER_ZERO_V) * slope;
  if (!isfinite(bar)) return NAN;
  if (fabs(bar) < 0.02f) bar = 0.0f; // deadband around atmospheric for noise
  return bar;
}

static void applyValve(ValveState v) {
  g_valve = v;
  digitalWrite(VALVE_PIN, v == OPEN ? HIGH : LOW);
}

static void applyHeaterBottom(bool on) {
  g_heater_bottom_on = on;
  digitalWrite(HEATER_BOTTOM_PIN, on ? HIGH : LOW);
}

static void applyHeaterExhaust(bool on) {
  g_heater_exhaust_on = on;
  digitalWrite(HEATER_EXHAUST_PIN, on ? HIGH : LOW);
}

static void setupPwm2kHz() {
  pinMode(PWM_PIN, OUTPUT);

  // Fast PWM, TOP = ICR4 (mode 14), non-inverting on OC4A, prescaler = 8
  TCCR4A = _BV(COM4A1) | _BV(WGM41);
  TCCR4B = _BV(WGM43)  | _BV(WGM42) | _BV(CS41);

  ICR4  = PWM_TOP;  // TOP -> 2 kHz
  OCR4A = 0;        // start at 0 %
}

static void setDuty(float frac) {
  if (!isfinite(frac)) frac = 0.0f;
  if (frac < 0.0f) frac = 0.0f;
  if (frac > 1.0f) frac = 1.0f;
  OCR4A = static_cast<uint16_t>(frac * PWM_TOP + 0.5f);
}

static float setPumpCommandPct(float pct) {
  if (!isfinite(pct)) pct = 0.0f;
  if (pct < 0.0f) pct = 0.0f;
  if (pct > PUMP_CMD_MAX_PCT) pct = PUMP_CMD_MAX_PCT;
  g_pump_cmd_pct = pct;
  setDuty(pct / 100.0f);
  return g_pump_cmd_pct;
}

// Modbus RTU CRC16
static uint16_t modbusCRC(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; ++b) {
      if (crc & 0x0001) {
        crc >>= 1;
        crc ^= 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

static float regsToFloatBE(const uint16_t *regs) {
  union {
    uint32_t u32;
    float f32;
  } value;
  value.u32 = (static_cast<uint32_t>(regs[0]) << 16) | regs[1];
  return value.f32;
}

// Read N_M_REG contiguous registers starting at M09 (FC=0x03)
static bool vfdReadM09toM12(uint16_t *vals) {
  uint8_t frame[8];

  frame[0] = VFD_SLAVE_ADDR;
  frame[1] = 0x03;             // Read Holding Registers
  frame[2] = REG_M09 >> 8;     // 0x08
  frame[3] = REG_M09 & 0xFF;   // 0x09
  frame[4] = 0x00;
  frame[5] = N_M_REG;          // 4 registers: M09..M12

  uint16_t crc = modbusCRC(frame, 6);
  frame[6] = crc & 0xFF;
  frame[7] = crc >> 8;

  while (VFD.available()) VFD.read(); // clear stale bytes

  VFD.write(frame, 8);
  VFD.flush();

  // Expected reply: addr, func, byteCount (=2*N), data(2*N), CRC(2)
  const uint8_t expectedLen = 3 + 2 * N_M_REG + 2; // 13 bytes
  uint8_t buf[32];
  uint8_t len = 0;
  unsigned long start = millis();

  while ((millis() - start) < 200 && len < expectedLen) {
    if (VFD.available()) {
      buf[len++] = static_cast<uint8_t>(VFD.read());
    }
  }

  if (len != expectedLen) {
    return false;
  }

  uint16_t crcResp = (uint16_t)buf[len - 1] << 8 | buf[len - 2];
  uint16_t crcCalc = modbusCRC(buf, len - 2);
  if (crcResp != crcCalc) {
    return false;
  }

  if (buf[0] != VFD_SLAVE_ADDR || buf[1] != 0x03) {
    return false;
  }

  uint8_t byteCount = buf[2];
  if (byteCount != 2 * N_M_REG) {
    return false;
  }

  for (uint8_t i = 0; i < N_M_REG; ++i) {
    uint8_t hi = buf[3 + 2 * i];
    uint8_t lo = buf[4 + 2 * i];
    vals[i] = ((uint16_t)hi << 8) | lo;
  }

  return true;
}

static bool pollVfd() {
  uint16_t vals[N_M_REG];
  const bool ok = vfdReadM09toM12(vals);
  g_vfd.lastPollMs = millis();
  if (!ok) {
    g_vfd.valid = false;
    g_vfd.freqHz = NAN;
    g_vfd.inputPowerPct = NAN;
    g_vfd.outputCurrentPct = NAN;
    g_vfd.outputVoltageV = NAN;
    return false;
  }

  g_vfd.valid = true;
  g_vfd.freqHz          = vals[0] / 100.0f;  // 0.01 Hz units
  g_vfd.inputPowerPct   = vals[1] / 100.0f;  // 0.01 %
  g_vfd.outputCurrentPct= vals[2] / 100.0f;  // 0.01 % of inverter rated current
  g_vfd.outputVoltageV  = vals[3] * 0.1f;    // 0.1 V units
  return true;
}

static bool flowReadMeasurements(uint16_t *vals) {
  uint8_t frame[8];

  frame[0] = FLOW_SLAVE_ADDR;
  frame[1] = 0x04;                  // Read Input Registers
  frame[2] = FLOW_REG_START >> 8;
  frame[3] = FLOW_REG_START & 0xFF;
  frame[4] = 0x00;
  frame[5] = FLOW_REG_COUNT;

  uint16_t crc = modbusCRC(frame, 6);
  frame[6] = crc & 0xFF;
  frame[7] = crc >> 8;

  while (FLOW.available()) FLOW.read();

  FLOW.write(frame, 8);
  FLOW.flush();

  const uint8_t expectedLen = 3 + 2 * FLOW_REG_COUNT + 2;
  uint8_t buf[32];
  uint8_t len = 0;
  unsigned long start = millis();

  while ((millis() - start) < 250 && len < expectedLen) {
    if (FLOW.available()) {
      buf[len++] = static_cast<uint8_t>(FLOW.read());
    }
  }

  if (len != expectedLen) {
    return false;
  }

  uint16_t crcResp = (uint16_t)buf[len - 1] << 8 | buf[len - 2];
  uint16_t crcCalc = modbusCRC(buf, len - 2);
  if (crcResp != crcCalc) {
    return false;
  }

  if (buf[0] != FLOW_SLAVE_ADDR || buf[1] != 0x04) {
    return false;
  }

  if (buf[2] != 2 * FLOW_REG_COUNT) {
    return false;
  }

  for (uint8_t i = 0; i < FLOW_REG_COUNT; ++i) {
    vals[i] = ((uint16_t)buf[3 + 2 * i] << 8) | buf[4 + 2 * i];
  }

  return true;
}

static bool pollFlowMeter() {
  uint16_t regs[FLOW_REG_COUNT];
  const bool ok = flowReadMeasurements(regs);
  g_flow.lastPollMs = millis();
  if (!ok) {
    g_flow.valid = false;
    g_flow.flowVelocityMps = NAN;
    g_flow.volumeFlowM3s = NAN;
    g_flow.massFlowKgS = NAN;
    g_flow.temperatureRaw = NAN;
    g_flow.densityKgM3 = NAN;
    return false;
  }

  g_flow.valid = true;
  g_flow.flowVelocityMps = regsToFloatBE(&regs[0]);
  g_flow.volumeFlowM3s   = regsToFloatBE(&regs[2]);
  g_flow.massFlowKgS     = regsToFloatBE(&regs[4]);
  g_flow.temperatureRaw  = regsToFloatBE(&regs[6]);
  g_flow.densityKgM3     = regsToFloatBE(&regs[8]);
  return true;
}

static void handleCommand(const String& s) {
  String cmd = s; cmd.trim();
  if (!cmd.length()) return;

  String upper = cmd; upper.toUpperCase();
  if (upper == "VALVE OPEN")       { g_mode = FORCE_OPEN;  applyValve(OPEN);   }
  else if (upper == "VALVE CLOSE") { g_mode = FORCE_CLOSE; applyValve(CLOSED); }
  else if (upper == "VALVE AUTO")  { g_mode = AUTO; }
  else if (upper == "HEATER BOTTOM ON")    { applyHeaterBottom(true); }
  else if (upper == "HEATER BOTTOM OFF")   { applyHeaterBottom(false); }
  else if (upper == "HEATER EXHAUST ON")   { applyHeaterExhaust(true); }
  else if (upper == "HEATER EXHAUST OFF")  { applyHeaterExhaust(false); }
  else if (upper.startsWith("PUMP")) {
    String rest = cmd.substring(4);
    rest.trim();
    String restUpper = rest; restUpper.toUpperCase();

    float pct = NAN;
    if (restUpper.startsWith("HZ")) {
      rest = rest.substring(2); rest.trim();
      float hz = rest.toFloat();
      if (isfinite(hz) && PUMP_MAX_FREQ_HZ > 0.0f) {
        pct = (hz / PUMP_MAX_FREQ_HZ) * 100.0f;
      }
    } else {
      if (rest.endsWith("%")) rest.remove(rest.length() - 1);
      pct = rest.toFloat();
    }

    if (isfinite(pct)) {
      float applied = setPumpCommandPct(pct);
      Serial.print(F("# Pump cmd set to "));
      Serial.print(applied, 3);
      Serial.println(F(" % of full-scale (analog)"));
    }
  }
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

static void emitTelemetry(const float temps[], size_t count, unsigned long nowMs,
                          float pressureBeforeBar, float pressureAfterBar, float pressureTankBar,
                          float pressureAfterVolts) {
  const float t_s = nowMs / 1000.0f;
  const char modeChar = (g_mode == AUTO) ? 'A' : (g_mode == FORCE_OPEN ? 'O' : 'C');

  Serial.print(F("{\"type\":\"telemetry\""));
  Serial.print(F(",\"t\":"));
  Serial.print(t_s, 3);

  Serial.print(F(",\"temps\":["));
  for (size_t i = 0; i < count; ++i) {
    const float v = (temps && isfinite(temps[i])) ? temps[i] : NAN;
    if (isfinite(v)) Serial.print(v, 2);
    else             Serial.print(F("null"));
    if (i + 1 < count) Serial.print(',');
  }
  Serial.print(']');

  Serial.print(F(",\"valve\":"));
  Serial.print((int)g_valve);

  Serial.print(F(",\"mode\":\""));
  Serial.print(modeChar);
  Serial.print('"');

  Serial.print(F(",\"pump\":{"));
  const float cmdPct  = g_pump_cmd_pct;
  const float cmdFrac = cmdPct / 100.0f;
  const float tgtHz   = PUMP_MAX_FREQ_HZ * cmdFrac;

  Serial.print(F("\"cmd_pct\":"));
  Serial.print(cmdPct, 3);
  Serial.print(F(",\"cmd_frac\":"));
  Serial.print(cmdFrac, 5);
  Serial.print(F(",\"cmd_hz\":"));
  Serial.print(tgtHz, 3);
  Serial.print(F(",\"max_freq_hz\":"));
  Serial.print(PUMP_MAX_FREQ_HZ, 1);
  Serial.print(F(",\"poll_ms\":"));
  Serial.print(g_vfd.lastPollMs);

  if (g_vfd.valid) {
    Serial.print(F(",\"freq_hz\":"));
    Serial.print(g_vfd.freqHz, 3);

    Serial.print(F(",\"freq_pct\":"));
    float freqPct = (PUMP_MAX_FREQ_HZ > 0.0f) ? (g_vfd.freqHz / PUMP_MAX_FREQ_HZ * 100.0f) : NAN;
    if (isfinite(freqPct)) Serial.print(freqPct, 2); else Serial.print(F("null"));

    Serial.print(F(",\"input_power_pct\":"));
    Serial.print(g_vfd.inputPowerPct, 2);
    if (VFD_RATED_POWER_W > 0.0f) {
      Serial.print(F(",\"input_power_w\":"));
      Serial.print(g_vfd.inputPowerPct * 0.01f * VFD_RATED_POWER_W, 1);
    }

    Serial.print(F(",\"output_current_pct\":"));
    Serial.print(g_vfd.outputCurrentPct, 2);
    if (VFD_RATED_CURRENT_A > 0.0f) {
      Serial.print(F(",\"output_current_a\":"));
      Serial.print(g_vfd.outputCurrentPct * 0.01f * VFD_RATED_CURRENT_A, 3);
    }

    Serial.print(F(",\"output_voltage_v\":"));
    Serial.print(g_vfd.outputVoltageV, 1);
    if (VFD_BASE_VOLTAGE > 0.0f) {
      Serial.print(F(",\"output_voltage_pct\":"));
      Serial.print(g_vfd.outputVoltageV / VFD_BASE_VOLTAGE * 100.0f, 1);
    }
  }

  Serial.print(F(",\"pressure_before_bar\":"));
  if (isfinite(pressureBeforeBar)) Serial.print(pressureBeforeBar, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_after_bar\":"));
  if (isfinite(pressureAfterBar)) Serial.print(pressureAfterBar, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_tank_bar\":"));
  if (isfinite(pressureTankBar)) Serial.print(pressureTankBar, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_before_bar_abs\":"));
  if (isfinite(pressureBeforeBar)) Serial.print(pressureBeforeBar + ATMOSPHERE_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_after_bar_abs\":"));
  if (isfinite(pressureAfterBar)) Serial.print(pressureAfterBar + ATMOSPHERE_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_tank_bar_abs\":"));
  if (isfinite(pressureTankBar)) Serial.print(pressureTankBar + ATMOSPHERE_BAR, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_after_v\":"));
  if (isfinite(pressureAfterVolts)) Serial.print(pressureAfterVolts, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_before_psi\":"));
  if (isfinite(pressureBeforeBar)) Serial.print(pressureBeforeBar * PSI_PER_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_after_psi\":"));
  if (isfinite(pressureAfterBar)) Serial.print(pressureAfterBar * PSI_PER_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_tank_psi\":"));
  if (isfinite(pressureTankBar)) Serial.print(pressureTankBar * PSI_PER_BAR, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_error_bar\":"));
  Serial.print(PRESSURE_ERR_BAR, 3);
  Serial.print('}');
  Serial.print(F(",\"fluid\":{"));
  Serial.print(F("\"name\":\""));
  Serial.print(FLUID_NAME);
  Serial.print(F("\",\"concentration_pct\":"));
  Serial.print(FLUID_CONC_PCT, 1);
  Serial.print(F(",\"meter_valid\":"));
  Serial.print(g_flow.valid ? 1 : 0);
  Serial.print(F(",\"meter_poll_ms\":"));
  Serial.print(g_flow.lastPollMs);

  if (g_flow.valid) {
    Serial.print(F(",\"flow_velocity_mps\":"));
    Serial.print(g_flow.flowVelocityMps, 6);
    Serial.print(F(",\"volume_flow_m3s\":"));
    Serial.print(g_flow.volumeFlowM3s, 9);
    Serial.print(F(",\"mass_flow_kgs\":"));
    Serial.print(g_flow.massFlowKgS, 9);
    Serial.print(F(",\"temperature_raw\":"));
    Serial.print(g_flow.temperatureRaw, 6);
    Serial.print(F(",\"density_kg_m3\":"));
    Serial.print(g_flow.densityKgM3, 6);
  }
  Serial.print('}');
  Serial.print(F(",\"heaters\":{"));
  Serial.print(F("\"bottom\":"));
  Serial.print(g_heater_bottom_on ? 1 : 0);
  Serial.print(F(",\"exhaust\":"));
  Serial.print(g_heater_exhaust_on ? 1 : 0);
  Serial.print('}');
  Serial.println('}');
}

void setup() {
  Serial.begin(115200);
  VFD.begin(VFD_BAUD, SERIAL_8E1);
  FLOW.begin(FLOW_BAUD, SERIAL_8E1);
  analogReference(DEFAULT);

  setupPwm2kHz();
  setPumpCommandPct(0.0f);  // start at 0% analog

  pinMode(VALVE_PIN, OUTPUT);
  applyValve(CLOSED);
  pinMode(HEATER_BOTTOM_PIN, OUTPUT);
  pinMode(HEATER_EXHAUST_PIN, OUTPUT);
  applyHeaterBottom(false);
  applyHeaterExhaust(false);

  pinMode(PRESSURE_PIN_BEFORE, INPUT);
  pinMode(PRESSURE_PIN_AFTER, INPUT);
  pinMode(PRESSURE_PIN_TANK, INPUT);

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

  // JSON line telemetry: temps[0..9] (°C), valve (0/1), mode (A/O/C), pump{}, fluid{}, heaters{}
  Serial.println(F("# Telemetry keys: temps[0..9] (°C), valve (0/1), mode (A/O/C), pump{} (VFD + pressures), fluid{} (MFC400), heaters{bottom,exhaust}"));
}

void loop() {
  // ── Serial command parser (non-blocking) ───────────────────────────────
  static String line;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (line.length()) handleCommand(line); line = ""; }
    else { line += c; if (line.length() > 64) line = ""; }
  }

  unsigned long now = millis();

  // ── Poll VFD (non-blocking 200 ms timeout inside) ──────────────────────
  if (now - lastVfdPoll >= VFD_POLL_MS) {
    lastVfdPoll = now;
    pollVfd();
  }

  if (now - lastFlowPoll >= FLOW_POLL_MS) {
    lastFlowPoll = now;
    pollFlowMeter();
  }

  // ── 1 Hz sampling ──────────────────────────────────────────────────────
  if (now - lastSample >= SAMPLE_INTERVAL_MS) {
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

    float pressureBeforeVolts = readPressureVolts(PRESSURE_PIN_BEFORE);
    float pressureAfterVolts  = readPressureVolts(PRESSURE_PIN_AFTER);
    float pressureTankVolts   = readPressureVolts(PRESSURE_PIN_TANK);

    float pressureBeforeBar = voltsToBar(pressureBeforeVolts);
    float pressureAfterBar  = voltsToBarAfter(pressureAfterVolts);
    float pressureTankBar   = voltsToBar(pressureTankVolts);

    emitTelemetry(temps_out, MAX_TCS_OUT, now,
                  pressureBeforeBar, pressureAfterBar, pressureTankBar,
                  pressureAfterVolts);
  }
}
