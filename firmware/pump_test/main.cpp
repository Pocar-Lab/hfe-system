#include <Arduino.h>

// --------------------------------------------------------
// PWM (0–10 V command) on pin 6 -> your 0–10 V converter
// --------------------------------------------------------

constexpr uint8_t  PWM_PIN   = 6;      // OC4A on Arduino Mega
constexpr uint16_t PWM_TOP   = 999;    // 2 kHz with prescaler 8

// Fractions of full-scale analog command (0.0–1.0)
// Keep within 0–5 % for gentle ramp on the FRENIC-Mini
constexpr float    PWM_STEPS[] = {
  0.00f,   // 0%
  0.025f,  // 2.5%
  0.050f   // 5%
};
constexpr size_t   N_PWM_STEPS = sizeof(PWM_STEPS) / sizeof(PWM_STEPS[0]);
constexpr unsigned long STEP_INTERVAL_MS = 8000UL;  // 8 s per step

size_t        currentStep    = 0;
unsigned long lastStepChange = 0;

void setupPwm2kHz() {
  pinMode(PWM_PIN, OUTPUT);

  // Fast PWM, TOP = ICR4 (mode 14), non-inverting on OC4A, prescaler = 8
  TCCR4A = _BV(COM4A1) | _BV(WGM41);
  TCCR4B = _BV(WGM43)  | _BV(WGM42) | _BV(CS41);

  ICR4  = PWM_TOP;      // TOP -> 2 kHz
  OCR4A = 0;            // start at 0%
}

void setDuty(float frac) {
  if (frac < 0.0f) frac = 0.0f;
  if (frac > 1.0f) frac = 1.0f;
  OCR4A = static_cast<uint16_t>(frac * PWM_TOP + 0.5f);
}

// --------------------------------------------------------
// Modbus RTU over Serial3, read-only (M09–M12)
// --------------------------------------------------------

HardwareSerial &VFD = Serial3;

constexpr uint8_t  SLAVE_ADDR = 1;     // y01
constexpr uint32_t BAUD       = 9600;  // y04
constexpr float    MOTOR_RATED_CURRENT_A = 2.8f;  // adjust to motor nameplate if you want A

// Correct Modbus addresses for M09–M12 (group M = 0x08):
// M09: output frequency (0.01 Hz)
// M10: input power (0.01 % of nominal motor output)
// M11: output current (0.01 % of inverter rated current)
// M12: output voltage (0.1 V)
constexpr uint16_t REG_M09 = 0x0809;
constexpr uint8_t  N_M_REG = 4;        // M09–M12

// Modbus RTU CRC16
uint16_t modbusCRC(const uint8_t *data, size_t len) {
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

// Read N_M_REG contiguous registers starting at M09 (FC=0x03)
bool vfdReadM09toM12(uint16_t *vals) {
  uint8_t frame[8];

  frame[0] = SLAVE_ADDR;
  frame[1] = 0x03;             // Read Holding Registers
  frame[2] = REG_M09 >> 8;     // 0x08
  frame[3] = REG_M09 & 0xFF;   // 0x09
  frame[4] = 0x00;
  frame[5] = N_M_REG;          // 4 registers: M09..M12

  uint16_t crc = modbusCRC(frame, 6);
  frame[6] = crc & 0xFF;
  frame[7] = crc >> 8;

  // Clear any stale bytes
  while (VFD.available()) VFD.read();

  // Send request
  VFD.write(frame, 8);
  VFD.flush();

  // Expected reply: addr, func, byteCount (=2*N), data(2*N), CRC(2)
  const uint8_t expectedLen = 3 + 2 * N_M_REG + 2; // 3 + 8 + 2 = 13
  uint8_t buf[32];
  uint8_t len = 0;
  unsigned long start = millis();

  while ((millis() - start) < 200 && len < expectedLen) {
    if (VFD.available()) {
      buf[len++] = static_cast<uint8_t>(VFD.read());
    }
  }

  if (len != expectedLen) {
    Serial.println(F("[VFD] Read timeout/short response for M09–M12"));
    return false;
  }

  // CRC check
  uint16_t crcResp = (uint16_t)buf[len - 1] << 8 | buf[len - 2];
  uint16_t crcCalc = modbusCRC(buf, len - 2);
  if (crcResp != crcCalc) {
    Serial.println(F("[VFD] CRC error on M09–M12 read"));
    return false;
  }

  if (buf[0] != SLAVE_ADDR || buf[1] != 0x03) {
    Serial.println(F("[VFD] Bad addr/func in reply"));
    return false;
  }

  uint8_t byteCount = buf[2];
  if (byteCount != 2 * N_M_REG) {
    Serial.println(F("[VFD] Unexpected byteCount"));
    return false;
  }

  for (uint8_t i = 0; i < N_M_REG; ++i) {
    uint8_t hi = buf[3 + 2 * i];
    uint8_t lo = buf[4 + 2 * i];
    vals[i] = ((uint16_t)hi << 8) | lo;
  }

  return true;
}

void printM09toM12() {
  uint16_t m[4];
  if (!vfdReadM09toM12(m)) {
    Serial.println(F("[VFD] Failed to read M09–M12"));
    return;
  }

  float outFreqHz    = m[0] / 100.0f;  // M09: 0.01 Hz units
  float inputPowerPc = m[1] / 100.0f;  // M10: 0.01 %
  float currentPct   = m[2] / 100.0f;  // M11: 0.01 % of inverter rated current
  float voltageV     = m[3] * 0.1f;    // M12: 0.1 V units

  Serial.print(F("M09 Output frequency = "));
  Serial.print(outFreqHz, 2);
  Serial.println(F(" Hz"));

  Serial.print(F("M10 Input power     = "));
  Serial.print(inputPowerPc, 2);
  Serial.println(F(" % of nominal motor power"));

  Serial.print(F("M11 Output current  = "));
  Serial.print(currentPct, 2);
  if (MOTOR_RATED_CURRENT_A > 0.0f) {
    float amps = (currentPct / 100.0f) * MOTOR_RATED_CURRENT_A;
    Serial.print(F(" % of rated (≈ "));
    Serial.print(amps, 3);
    Serial.print(F(" A)"));
  }
  Serial.println();

  Serial.print(F("M12 Output voltage  = "));
  Serial.print(voltageV, 1);
  Serial.println(F(" V"));
}

// --------------------------------------------------------
// Setup / loop
// --------------------------------------------------------

unsigned long lastPoll = 0;
constexpr unsigned long POLL_INTERVAL_MS = 1000UL;  // read M09–M12 every 1 s

void setup() {
  setupPwm2kHz();

  Serial.begin(115200);
  delay(500);
  Serial.println(F("\nPWM (0–10 V) command + VFD monitor (M09–M12 via Modbus RTU, group 0x08)"));

  // Start with lowest analog command (5% of full-scale)
  currentStep    = 0;
  lastStepChange = millis();
  setDuty(PWM_STEPS[currentStep]);
  Serial.print(F("Initial analog command = "));
  Serial.print(PWM_STEPS[currentStep] * 100.0f, 1);
  Serial.println(F(" % of full scale"));

  // Modbus: 9600 baud, 8E1 to match y04/y05/y06/y07
  VFD.begin(BAUD, SERIAL_8E1);
}

void loop() {
  unsigned long now = millis();

  // Step the analog command every STEP_INTERVAL_MS
  if (now - lastStepChange >= STEP_INTERVAL_MS) {
    lastStepChange = now;
    currentStep = (currentStep + 1) % N_PWM_STEPS;
    float frac = PWM_STEPS[currentStep];
    setDuty(frac);

    Serial.println();
    Serial.print(F("Changed analog command to "));
    Serial.print(frac * 100.0f, 1);
    Serial.println(F(" % of full scale"));
  }

  // Poll VFD once per second
  if (now - lastPoll >= POLL_INTERVAL_MS) {
    lastPoll = now;
    Serial.println(F("----- VFD Snapshot -----"));
    printM09toM12();
    Serial.println();
  }
}
