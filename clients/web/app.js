'use strict';

(function () {
  const MAX_SENSORS = 10;
  const MAX_POINTS = 900;
  const WINDOW_MINUTES = 15;
  const DEFAULT_HFE_GOAL_C = -110.0;
  const DEFAULT_HX_LIMIT_C = -120.0;
  const DEFAULT_AUTO_HYSTERESIS_C = 0.5;
  const DEFAULT_HX_APPROACH_C = 10.0;
  const AUTO_TARGET_EPSILON_C = 0.005;
  const THERMOCOUPLE_DISPLAY_DIGITS = 0;
  const PUMP_MAX_CMD_PCT = 100.0;
  const PUMP_MAX_FREQ_HZ = 71.7;
  const PUMP_SAFE_MAX_HZ = 60.0;
  const PUMP_DEFAULT_START_PCT = 5.0;
  const PUMP_DELTA_P_ESTOP_LIMIT_BAR = 5.0;
  const PUMP_SAFETY_LAW_KEY = 'pump_delta_p_high';
  const PUMP_SAFETY_LAW_LABEL = 'Pump delta P high';
  const PUMP_EST_RPM_PER_HZ = 30.0;
  const PUMP_NAMEPLATE_CURRENT_A = 3.4;
  const PUMP_BASE_VOLTAGE_V = 230.0;
  const PUMP_RATED_OUTPUT_W = 746.0;
  const PUMP_NAMEPLATE_EFFICIENCY = 0.855;
  const PUMP_EST_RATED_INPUT_W = PUMP_RATED_OUTPUT_W / PUMP_NAMEPLATE_EFFICIENCY;
  const TTI_SENSOR_INDEX = 4;
  const TTO_SENSOR_INDEX = 6;
  const FLUID_REFERENCE = {
    name: 'HFE-7200',
    concentrationPct: 100.0,
  };
  // Fallback for older supervisor payloads; the MFC400 Modbus supplement documents 30006 in Kelvin.
  const FLOW_TEMPERATURE_SOURCE_UNIT = 'kelvin';
  const PUMP_LOG_FIELDS = [
    { column: 'pump_cmd_pct', key: 'cmd_pct', digits: 3 },
    { column: 'pump_freq_hz', key: 'freq_hz', digits: 2 },
    { column: 'pump_rotation_speed_rpm', key: 'rotation_speed_rpm', digits: 0 },
    { column: 'pump_input_power_kw', key: 'input_power_kw', digits: 2 },
    { column: 'pump_input_power_w', key: 'input_power_w', digits: 0 },
    { column: 'pump_output_current_a', key: 'output_current_a', digits: 2 },
    { column: 'pump_output_voltage_v', key: 'output_voltage_v', digits: 1 },
    { column: 'pump_pressure_before_bar_abs', key: 'pressure_before_bar_abs', digits: 3 },
    { column: 'pump_pressure_after_bar_abs', key: 'pressure_after_bar_abs', digits: 3 },
    { column: 'pump_pressure_tank_bar_abs', key: 'pressure_tank_bar_abs', digits: 3 },
    { column: 'pump_pressure_error_bar', key: 'pressure_error_bar', digits: 3 },
    { column: 'pump_max_freq_hz', key: 'max_freq_hz', digits: 1 },
  ];
  const FLUID_LOG_FIELDS = [
    { column: 'fluid_meter_valid', key: 'meter_valid', digits: 0 },
    { column: 'fluid_concentration_pct', key: 'concentration_pct', digits: 1 },
    { column: 'fluid_flow_velocity_mps', key: 'flow_velocity_mps', digits: 6 },
    { column: 'fluid_volume_flow_m3s', key: 'volume_flow_m3s', digits: 9 },
    { column: 'fluid_mass_flow_kgs', key: 'mass_flow_kgs', digits: 9 },
    { column: 'fluid_temperature_c', key: 'temperature_c', digits: 3 },
    { column: 'fluid_density_kg_m3', key: 'density_kg_m3', digits: 0 },
    { column: 'fluid_delta_p_bar', key: 'delta_p_bar', digits: 3 },
  ];
  const SCALE_LOG_FIELDS = [
    { column: 'scale_weight_kg', key: 'weight_kg', digits: 3 },
    { column: 'scale_age_s', key: 'age_s', digits: 3 },
    { column: 'scale_tare_kg', key: 'tare_kg', digits: 3 },
  ];
  const TEMP_LOG_COLUMNS = ['U0_C', 'U1_C', 'TTEST_C', 'TFO_C', 'TTI_C', 'U5_C', 'TTO_C', 'TMI_C', 'THI_C', 'THM_C'];
  const LOG_HEADER = [
    'time_s',
    ...TEMP_LOG_COLUMNS,
    'valve',
    'mode',
    ...PUMP_LOG_FIELDS.map((field) => field.column),
    ...FLUID_LOG_FIELDS.map((field) => field.column),
    ...SCALE_LOG_FIELDS.map((field) => field.column),
  ];
  const LOG_FIELD_DIGITS = new Map(
    [...PUMP_LOG_FIELDS, ...FLUID_LOG_FIELDS, ...SCALE_LOG_FIELDS].map((field) => [field.column, field.digits ?? 3]),
  );

  const params = new URLSearchParams(window.location.search);
  const tokenParam = params.get('token') || '';
  const authHeaderValue = tokenParam
    ? tokenParam.toLowerCase().startsWith('bearer ')
      ? tokenParam
      : `Bearer ${tokenParam}`
    : '';

  const statusEl = document.getElementById('connection-status');
  const loggingStatusEl = document.getElementById('logging-status');
  const commandStatusEl = document.getElementById('command-status');
  const valveStateEl = document.getElementById('valve-state');
  const modeStateEl = document.getElementById('mode-state');
  const heaterBottomStateEl = document.getElementById('heater-bottom-state');
  const heaterExhaustStateEl = document.getElementById('heater-exhaust-state');
  // pump overview + controls
  const overviewPumpSpeedEl = document.getElementById('overview-pump-speed');
  const overviewPumpSpeedSubEl = document.getElementById('overview-pump-speed-sub');
  const pumpCmdForm = document.getElementById('pump-command-form');
  const pumpCmdInput = document.getElementById('pump-command-input');
  const pumpCmdSlider = document.getElementById('pump-command-slider');
  const pumpOverspeedToggle = document.getElementById('pump-overspeed-toggle');
  const globalPumpStopButton = document.getElementById('global-pump-stop-button');
  const pumpStopButton = document.getElementById('pump-stop-button');
  const pumpSafetyStatusEl = document.getElementById('pump-safety-status');
  const pumpSpeedSubmitButton = pumpCmdForm ? pumpCmdForm.querySelector('button[type="submit"]') : null;
  const pumpRunStateEl = document.getElementById('pump-run-state');
  const pumpCmdHzEl = document.getElementById('pump-cmd-hz');
  const pumpCmdRpmEl = document.getElementById('pump-cmd-rpm');
  const pumpCmdFlowEl = document.getElementById('pump-cmd-flow');
  const pumpPressureBeforeEl = document.getElementById('pump-pressure-before');
  const pumpPressureAfterEl = document.getElementById('pump-pressure-after');
  const pumpPressureBeforeUnitEl = document.getElementById('pump-pressure-before-unit');
  const pumpPressureAfterUnitEl = document.getElementById('pump-pressure-after-unit');
  const vfdFrequencyEl = document.getElementById('vfd-frequency');
  const vfdFrequencyPctEl = document.getElementById('vfd-frequency-pct');
  const vfdCurrentEl = document.getElementById('vfd-current');
  const vfdCurrentPctEl = document.getElementById('vfd-current-pct');
  const vfdVoltageEl = document.getElementById('vfd-voltage');
  const vfdVoltagePctEl = document.getElementById('vfd-voltage-pct');
  const vfdPowerEl = document.getElementById('vfd-power');
  const vfdPowerPctEl = document.getElementById('vfd-power-pct');
  const vfdPowerUnitEl = document.getElementById('vfd-power-unit');
  const vfdSpeedEl = document.getElementById('vfd-speed');
  const vfdSpeedSubEl = document.getElementById('vfd-speed-sub');
  const sensorCountEl = document.getElementById('sensor-count');
  const validCountEl = document.getElementById('valid-count');
  const validListEl = document.getElementById('valid-list');
  const overviewConnectionEl = document.getElementById('overview-connection');
  const overviewValveEl = document.getElementById('overview-valve');
  const overviewPumpDeltaPEl = document.getElementById('overview-pump-delta-p');
  const overviewPumpDeltaPSubEl = document.getElementById('overview-pump-delta-p-sub');
  const overviewScaleWeightEl = document.getElementById('overview-scale-weight');
  const overviewScaleStatusEl = document.getElementById('overview-scale-status');
  const sensorValuesEl = document.getElementById('sensor-values');
  const loggingToggleBtn = document.getElementById('logging-toggle');
  const fluidNameEl = document.getElementById('fluid-name');
  const fluidConcentrationEl = document.getElementById('fluid-concentration');
  const fluidTankPressureEl = document.getElementById('fluid-tank-pressure');
  const fluidTankPressureSubEl = document.getElementById('fluid-tank-pressure-sub');
  const fluidFlowVelocityEl = document.getElementById('fluid-flow-velocity');
  const fluidVolumeFlowEl = document.getElementById('fluid-volume-flow');
  const fluidMassFlowEl = document.getElementById('fluid-mass-flow');
  const fluidTemperatureEl = document.getElementById('fluid-temperature');
  const fluidDensityEl = document.getElementById('fluid-density');
  const fluidTempRiseEl = document.getElementById('fluid-temp-rise');
  const fluidViscosityEl = document.getElementById('fluid-viscosity');
  const fluidMixingEfficiencyEl = document.getElementById('fluid-mixing-efficiency');
  const autoControlForm = document.getElementById('auto-control-form');
  const hfeGoalInput = document.getElementById('hfe-goal-input');
  const hxLimitInput = document.getElementById('hx-limit-input');
  const autoHysteresisInput = document.getElementById('auto-hysteresis-input');
  const hxApproachInput = document.getElementById('hx-approach-input');
  const autoModeStatusEl = document.getElementById('auto-mode-status');
  const scaleTareForm = document.getElementById('scale-tare-form');
  const scaleTareInput = document.getElementById('scale-tare-input');
  const scaleTareStatusEl = document.getElementById('scale-tare-status');
  const chartSectionEl = document.getElementById('chart-section');
  const chartPanelEls = Array.from(chartSectionEl ? chartSectionEl.querySelectorAll('.chart-panel') : []);
  const tempChartCanvas = document.getElementById('temp-chart');
  const pressureChartCanvas = document.getElementById('pressure-chart');
  const statsSectionEl = document.getElementById('stats-section');
  const controlsSectionEl = document.getElementById('controls-section');
  const headerEl = document.querySelector('header');
  const statusStripEl = document.getElementById('status-strip');
  const pageButtons = Array.from(document.querySelectorAll('#page-tabs .page-tab'));
  const pagePanels = Array.from(document.querySelectorAll('[data-page-panel]'));
  const heroLinkButtons = Array.from(document.querySelectorAll('.page-link[data-target-page]'));
  let activePage = 'general';

  function setActivePage(page) {
    const target = page || 'general';
    if (target === activePage) {
      return;
    }
    activePage = target;
    pageButtons.forEach((btn) => {
      const match = (btn.dataset.page || 'general') === activePage;
      btn.classList.toggle('active', match);
      btn.setAttribute('aria-pressed', match ? 'true' : 'false');
    });
    pagePanels.forEach((panel) => {
      const match = (panel.dataset.pagePanel || 'general') === activePage;
      panel.classList.toggle('active', match);
    });
    scheduleChartHeightUpdate();
  }

  if (pageButtons.length) {
    const initialButton = pageButtons.find((btn) => btn.classList.contains('active'));
    if (initialButton) {
      activePage = initialButton.dataset.page || 'general';
    }
  }

  pageButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      setActivePage(btn.dataset.page || 'general');
    });
  });
  heroLinkButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      setActivePage(btn.dataset.targetPage || 'general');
    });
  });

  const tempCtx = tempChartCanvas ? tempChartCanvas.getContext('2d') : null;
  const pressureCtx = pressureChartCanvas ? pressureChartCanvas.getContext('2d') : null;
  const ChartCtor = window.Chart;
  const customCss = getComputedStyle(document.documentElement);
  const legendLabelColor = customCss.getPropertyValue('--chart-text').trim() || '#888';
  const gridColor = customCss.getPropertyValue('--chart-grid').trim() || 'rgba(0,0,0,0.1)';
  const tickColor = legendLabelColor;

  const SENSOR_COLORS = [
    '#4cc9f0',
    '#4895ef',
    '#4361ee',
    '#3f37c9',
    '#3a0ca3',
    '#7209b7',
    '#b5179e',
    '#f72585',
    '#ff6f59',
    '#ff9f1c',
  ];
  const PRESSURE_COLORS = ['#2d82ff', '#f7b731', '#2ecc71'];
  const PRESSURE_SENSOR_METADATA = [
    { key: 'pressure_before_bar_abs', tag: 'PMI', label: 'Pump inlet' },
    { key: 'pressure_after_bar_abs', tag: 'PMO', label: 'Pump outlet' },
    { key: 'pressure_tank_bar_abs', tag: 'PTA', label: 'Tank' },
  ];
  const SENSOR_METADATA = [
    { tag: 'U0', label: 'Unassigned', connected: false },
    { tag: 'U1', label: 'Unassigned', connected: false },
    { tag: 'TTEST', label: 'Test thermocouple', connected: true },
    { tag: 'TFO', label: 'Flow meter outlet', connected: true },
    { tag: 'TTI', label: 'Tank inlet', connected: true },
    { tag: 'U5', label: 'Unassigned', connected: false },
    { tag: 'TTO', label: 'Tank outlet', connected: true },
    { tag: 'TMI', label: 'Pump inlet', connected: true },
    { tag: 'THI', label: 'HEX inlet', connected: true },
    { tag: 'THM', label: 'HEX middle', connected: true },
  ];
  const CONNECTED_SENSOR_INDICES = SENSOR_METADATA.reduce((indices, meta, index) => {
    if (meta && meta.connected) {
      indices.push(index);
    }
    return indices;
  }, []);

  function createLineDataset(label, color, extra = {}) {
    return {
      label,
      borderColor: color,
      backgroundColor: 'rgba(0,0,0,0)',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.1,
      spanGaps: true,
      data: [],
      ...extra,
    };
  }

  function sensorMeta(index) {
    return SENSOR_METADATA[index] || { tag: `U${index}`, label: 'Unassigned', connected: false };
  }

  function sensorShortName(index) {
    return sensorMeta(index).tag;
  }

  function sensorLongName(index) {
    const meta = sensorMeta(index);
    return meta.connected ? `${meta.tag} (${meta.label})` : `U${index}`;
  }

  function visibleSensorIndices(sensorCount = MAX_SENSORS) {
    return CONNECTED_SENSOR_INDICES.filter((index) => index < sensorCount);
  }

  const sensorDatasets = Array.from({ length: MAX_SENSORS }, (_, idx) =>
    createLineDataset(sensorShortName(idx), SENSOR_COLORS[idx % SENSOR_COLORS.length]),
  );
  const pressureDatasets = [
    createLineDataset('PMI', PRESSURE_COLORS[0]),
    createLineDataset('PMO', PRESSURE_COLORS[1]),
    createLineDataset('PTA', PRESSURE_COLORS[2]),
  ];

  function sentenceCase(text) {
    const value = text === undefined || text === null ? '' : String(text);
    const trimmed = value.trim();
    if (!trimmed) {
      return '';
    }
    return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
  }

  let currentHfeGoalC = DEFAULT_HFE_GOAL_C;
  let currentHxLimitC = DEFAULT_HX_LIMIT_C;
  let currentAutoHysteresisC = DEFAULT_AUTO_HYSTERESIS_C;
  let currentHxApproachC = DEFAULT_HX_APPROACH_C;
  let pumpMaxFreqHz = PUMP_MAX_FREQ_HZ;
  let lastPumpCmdPct = 0;
  let pumpRunning = false;
  let userPumpDirty = false;
  let overspeedEnabled = false;
  let scaleTareKg = 0.0;
  let pumpSafetyState = buildPumpSafetyModel(null, null);
  const localAutoTargetOverrides = new Set();

  function hfeGoalLabel() {
    return `HFE goal (${currentHfeGoalC.toFixed(1)} °C)`;
  }

  function nearlyEqualTemperature(a, b) {
    return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= AUTO_TARGET_EPSILON_C;
  }

  function canUseTelemetryAutoTarget(key, telemetryValue, currentValue) {
    if (!Number.isFinite(telemetryValue)) {
      return false;
    }
    if (!localAutoTargetOverrides.has(key)) {
      return true;
    }
    if (nearlyEqualTemperature(telemetryValue, currentValue)) {
      localAutoTargetOverrides.delete(key);
      return true;
    }
    return false;
  }

  function markLocalAutoTargets() {
    localAutoTargetOverrides.add('hfeGoal');
    localAutoTargetOverrides.add('hxLimit');
    localAutoTargetOverrides.add('hysteresis');
    localAutoTargetOverrides.add('hxApproach');
  }

  function formatThermocoupleValue(value) {
    const num = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(num) ? num.toFixed(THERMOCOUPLE_DISPLAY_DIGITS) : '—';
  }

  function formatThermocoupleTemperature(value) {
    const formatted = formatThermocoupleValue(value);
    return formatted === '—' ? formatted : `${formatted} °C`;
  }

  function formatPressureValue(value, digits = 3) {
    const num = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(num) ? `${num.toFixed(digits)} bar` : '—';
  }

  function setTone(el, tone = '') {
    if (!el) {
      return;
    }
    if (tone) {
      el.dataset.tone = tone;
    } else {
      delete el.dataset.tone;
    }
  }

  function formatTemperatureSummary(value, digits = 1) {
    const num = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(num) ? `${num.toFixed(digits)} °C` : '—';
  }

  function updateAutoModeStatus(control) {
    if (!autoModeStatusEl) {
      return;
    }

    const autoControl = control && typeof control === 'object' ? control : null;
    if (!autoControl) {
      autoModeStatusEl.textContent = `Auto targets configured locally. Waiting for firmware auto-mode status telemetry. HFE goal ${formatTemperatureSummary(
        currentHfeGoalC,
      )}, HX limit ${formatTemperatureSummary(currentHxLimitC)}, HX approach ${formatTemperatureSummary(
        currentHxApproachC,
      )}, hysteresis ${formatTemperatureSummary(currentAutoHysteresisC)}.`;
      setTone(autoModeStatusEl, 'info');
      return;
    }

    const thiTempC = finiteNumber(autoControl.thi_temp_c);
    const flowTempC = finiteNumber(autoControl.flow_temp_c);
    const thiReopenC = finiteNumber(autoControl.thi_reopen_c);
    const flowReopenC = finiteNumber(autoControl.flow_reopen_c);
    const closeRequested = coerceOnOff(autoControl.close_requested) === true;
    const readyToOpen = coerceOnOff(autoControl.ready_to_open) === true;
    const autoCloseLatched = coerceOnOff(autoControl.auto_close_latched) === true;
    const withinBand = coerceOnOff(autoControl.within_hysteresis_band) === true;
    const reason =
      typeof autoControl.auto_close_reason === 'string'
        ? autoControl.auto_close_reason.trim().toLowerCase()
        : 'none';
    const manualMode = latestSnapshot && latestSnapshot.modeChar && latestSnapshot.modeChar !== 'A';
    const valveOpen = latestSnapshot && Number(latestSnapshot.valve) !== 0;

    let text = '';
    let tone = '';

    if (manualMode) {
      text = `Auto is configured with HFE goal ${formatTemperatureSummary(
        currentHfeGoalC,
      )}, HX limit ${formatTemperatureSummary(currentHxLimitC)}, HX approach ${formatTemperatureSummary(
        currentHxApproachC,
      )}, and hysteresis ${formatTemperatureSummary(currentAutoHysteresisC)}.`;
      tone = 'warn';
    } else if (closeRequested) {
      if (reason === 'missing_thi') {
        text = 'Auto is holding the LN valve closed because THI telemetry is unavailable.';
      } else if (reason === 'missing_flow_temp') {
        text = 'Auto is holding the LN valve closed because flow-meter temperature telemetry is unavailable.';
      } else if (reason === 'thi_limit') {
        text = `Auto is holding the LN valve closed because THI is ${formatTemperatureSummary(
          thiTempC,
        )}, at or below the HX limit ${formatTemperatureSummary(currentHxLimitC)}. It can reopen once THI rises above ${formatTemperatureSummary(
          thiReopenC,
        )} (flow − ${formatTemperatureSummary(currentHxApproachC)}) and the flow meter is above ${formatTemperatureSummary(flowReopenC)}.`;
      } else if (reason === 'flow_goal') {
        text = `Auto is holding the LN valve closed because flow-meter temperature is ${formatTemperatureSummary(
          flowTempC,
        )}, at or below the HFE goal ${formatTemperatureSummary(currentHfeGoalC)}. It can reopen once THI rises above ${formatTemperatureSummary(
          thiReopenC,
        )} (flow − ${formatTemperatureSummary(currentHxApproachC)}) and the flow meter is above ${formatTemperatureSummary(flowReopenC)}.`;
      } else {
        text = 'Auto is holding the LN valve closed.';
      }
      tone = 'warn';
    } else if (readyToOpen) {
      text = `Auto ${valveOpen ? 'has the LN valve open' : 'is ready to open the LN valve'}. THI is ${formatTemperatureSummary(
        thiTempC,
      )} and flow-meter temperature is ${formatTemperatureSummary(
        flowTempC,
      )}, both above their reopen thresholds.`;
      tone = 'success';
    } else if (withinBand || autoCloseLatched) {
      text = `Auto is holding the LN valve closed until the hysteresis band clears. Reopen thresholds are THI ${formatTemperatureSummary(
        thiReopenC,
      )} (flow − ${formatTemperatureSummary(currentHxApproachC)}) and flow-meter temperature ${formatTemperatureSummary(flowReopenC)}.`;
      tone = 'info';
    } else {
      text = `Auto close thresholds are clear; the LN valve is ${
        valveOpen ? 'open' : 'allowed to open'
      }. THI reopen threshold ${formatTemperatureSummary(thiReopenC)} and flow-meter reopen threshold ${formatTemperatureSummary(
        flowReopenC,
      )} apply after an auto close.`;
      tone = 'success';
    }

    autoModeStatusEl.textContent = text;
    setTone(autoModeStatusEl, tone);
  }

  function createTimeScale() {
    return {
      type: 'linear',
      title: { display: true, text: 'Time (min)' },
      min: 0,
      max: WINDOW_MINUTES,
      ticks: { color: tickColor },
      grid: { color: gridColor },
    };
  }

  function createLegendOptions(filter) {
    const options = {
      labels: {
        color: legendLabelColor,
      },
    };
    if (typeof filter === 'function') {
      options.labels.filter = filter;
    }
    return options;
  }

  const setpointDataset = {
    label: hfeGoalLabel(),
    borderColor: '#adb5bd',
    borderWidth: 1,
    borderDash: [6, 6],
    pointRadius: 0,
    tension: 0,
    spanGaps: true,
    data: [],
  };

  const chart = tempCtx && ChartCtor
    ? new ChartCtor(tempCtx, {
        type: 'line',
        data: {
          datasets: [...sensorDatasets, setpointDataset],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          parsing: false,
          animation: false,
          interaction: {
            intersect: false,
            mode: 'nearest',
          },
          scales: {
            x: createTimeScale(),
            y: {
              title: { display: true, text: 'Temperature (°C)' },
              suggestedMin: -170,
              suggestedMax: 25,
              ticks: {
                color: tickColor,
                callback(value) {
                  return formatThermocoupleValue(value);
                },
              },
              grid: { color: gridColor },
            },
          },
          plugins: {
            legend: createLegendOptions((legendItem, data) => {
              const dataset = data.datasets[legendItem.datasetIndex];
              if (dataset === setpointDataset) {
                return true;
              }
              return CONNECTED_SENSOR_INDICES.includes(legendItem.datasetIndex);
            }),
            tooltip: {
              callbacks: {
                label(context) {
                  const datasetLabel =
                    context.dataset && context.dataset.label ? `${context.dataset.label}: ` : '';
                  const yValue =
                    context.parsed && typeof context.parsed.y === 'number'
                      ? context.parsed.y
                      : Number.NaN;
                  if (context.dataset === setpointDataset) {
                    return Number.isFinite(yValue)
                      ? `${datasetLabel}${yValue.toFixed(1)} °C`
                      : `${datasetLabel}—`;
                  }
                  return `${datasetLabel}${formatThermocoupleTemperature(yValue)}`;
                },
              },
            },
          },
        },
      })
    : null;

  const pressureChart = pressureCtx && ChartCtor
    ? new ChartCtor(pressureCtx, {
        type: 'line',
        data: {
          datasets: pressureDatasets,
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          parsing: false,
          animation: false,
          interaction: {
            intersect: false,
            mode: 'nearest',
          },
          scales: {
            x: createTimeScale(),
            y: {
              title: { display: true, text: 'Pressure (bar abs)' },
              min: 1,
              max: 10,
              ticks: {
                color: tickColor,
                stepSize: 1,
                callback(value) {
                  const num = typeof value === 'number' ? value : Number(value);
                  return Number.isFinite(num) ? num.toFixed(2) : '—';
                },
              },
              grid: { color: gridColor },
            },
          },
          plugins: {
            legend: createLegendOptions(),
            tooltip: {
              callbacks: {
                label(context) {
                  const datasetLabel =
                    context.dataset && context.dataset.label ? `${context.dataset.label}: ` : '';
                  const yValue =
                    context.parsed && typeof context.parsed.y === 'number'
                      ? context.parsed.y
                      : Number.NaN;
                  return `${datasetLabel}${formatPressureValue(yValue)}`;
                },
              },
            },
          },
        },
      })
    : null;

  const MIN_CHART_CONTENT_HEIGHT = 300;
  const MAX_CHART_CONTENT_HEIGHT = 900;
  let pendingChartHeightFrame = null;
  let chartResizeObserver = null;
  setActivePage(activePage);

  function computeChartContentHeight() {
    if (!chartSectionEl) {
      return null;
    }
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    if (!viewportHeight) {
      return null;
    }

    let reserved = 0;
    if (headerEl) {
      reserved += headerEl.offsetHeight;
    }
    if (statusStripEl) {
      reserved += statusStripEl.offsetHeight;
    }

    const mainEl = chartSectionEl.parentElement;
    if (mainEl) {
      const mainStyle = window.getComputedStyle(mainEl);
      reserved += parseFloat(mainStyle.paddingTop) || 0;
      reserved += parseFloat(mainStyle.paddingBottom) || 0;

      const children = Array.from(mainEl.children);
      const rowGap = parseFloat(mainStyle.rowGap || mainStyle.gap || 0);
      if (rowGap && children.length > 1) {
        reserved += rowGap * (children.length - 1);
      }
      for (const child of children) {
        if (child !== chartSectionEl) {
          reserved += child.offsetHeight;
        }
      }
    }

    const sectionStyle = window.getComputedStyle(chartSectionEl);
    const chartExtras =
      (parseFloat(sectionStyle.paddingTop) || 0) +
      (parseFloat(sectionStyle.paddingBottom) || 0) +
      (parseFloat(sectionStyle.borderTopWidth) || 0) +
      (parseFloat(sectionStyle.borderBottomWidth) || 0);

    return viewportHeight - reserved - chartExtras;
  }

  function applyChartHeight() {
    if (!chartSectionEl) {
      return;
    }
    const available = computeChartContentHeight();
    if (!Number.isFinite(available)) {
      return;
    }
    const target = Math.max(MIN_CHART_CONTENT_HEIGHT, Math.min(available, MAX_CHART_CONTENT_HEIGHT));
    const rowCount = window.matchMedia('(max-width: 1100px)').matches
      ? Math.max(chartPanelEls.length, 1)
      : 1;
    const sectionStyle = window.getComputedStyle(chartSectionEl);
    const sectionGap = parseFloat(sectionStyle.rowGap || sectionStyle.gap || 0);
    const sectionHeight = Math.round(target * rowCount + sectionGap * Math.max(rowCount - 1, 0));
    const currentHeight = parseFloat(chartSectionEl.style.height || 0);
    if (Number.isFinite(currentHeight) && Math.abs(currentHeight - sectionHeight) < 1) {
      return;
    }
    chartSectionEl.style.height = `${sectionHeight}px`;
    chartSectionEl.style.minHeight = `${sectionHeight}px`;
    if (chart) {
      chart.resize();
    }
    if (pressureChart) {
      pressureChart.resize();
    }
  }

  function scheduleChartHeightUpdate() {
    if (pendingChartHeightFrame !== null) {
      return;
    }
    pendingChartHeightFrame = requestAnimationFrame(() => {
      pendingChartHeightFrame = null;
      applyChartHeight();
    });
  }

  scheduleChartHeightUpdate();
  window.addEventListener('resize', scheduleChartHeightUpdate);

  if (typeof ResizeObserver !== 'undefined') {
    chartResizeObserver = new ResizeObserver(() => {
      scheduleChartHeightUpdate();
    });
    [statsSectionEl, controlsSectionEl, headerEl, statusStripEl].forEach((el) => {
      if (el) {
        chartResizeObserver.observe(el);
      }
    });
  }

  let ws = null;
  let reconnectDelay = 1000;
  let startEpochSec = null;
  const sensorSeries = Array.from({ length: MAX_SENSORS }, () => []);
  const setpointSeries = [];
  const pressureSeries = pressureDatasets.map(() => []);

  let latestSnapshot = null;
  let loggingEnabled = false;
  let loggingRows = [];
  let serverLogInfo = { active: false, filename: null, path: null, rows: 0 };

  function updateLoggingButtonState({ busy = false } = {}) {
    if (!loggingToggleBtn) {
      return;
    }
    const active = loggingEnabled || serverLogInfo.active;
    loggingToggleBtn.textContent = active ? 'Stop Logging' : 'Start Logging';
    loggingToggleBtn.classList.toggle('primary', !active);
    loggingToggleBtn.classList.toggle('danger', active);
    loggingToggleBtn.disabled = busy;
  }

  function setConnectionStatus(text, tone = 'normal') {
    const formatted = sentenceCase(text);
    if (statusEl) {
      statusEl.textContent = `Status: ${formatted}`;
      statusEl.dataset.tone = tone;
    }
    if (overviewConnectionEl) {
      overviewConnectionEl.textContent = formatted || '—';
      overviewConnectionEl.dataset.tone = tone;
    }
  }

  function setLoggingStatus(text) {
    const formatted = sentenceCase(text);
    if (loggingStatusEl) {
      loggingStatusEl.textContent = `Logging: ${formatted}`;
    }
  }

  function updateLoggingStatusLabel() {
    const parts = [];
    if (serverLogInfo.active) {
      const serverLabel = serverLogInfo.filename || 'server log';
      const serverRows = typeof serverLogInfo.rows === 'number' ? serverLogInfo.rows : 0;
      parts.push(`${serverLabel} (${serverRows} rows)`);
    }
    if (loggingEnabled) {
      parts.push(`download buffer: ${loggingRows.length} rows`);
    }
    if (parts.length) {
      setLoggingStatus(`on (${parts.join(' | ')})`);
    } else {
      setLoggingStatus('off');
    }
    scheduleChartHeightUpdate();
  }

  function extractLogValues(source, fields) {
    const src = source && typeof source === 'object' ? source : null;
    return fields.map((field) => {
      const raw = src ? src[field.key] : null;
      if (raw === null || raw === undefined) {
        return NaN;
      }
      const num = typeof raw === 'number' ? raw : Number(raw);
      return Number.isFinite(num) ? num : NaN;
    });
  }

  function formatLogValue(column, value) {
    if (column === 'time_s') {
      const num = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(num) ? num.toFixed(3) : 'nan';
    }
    if (column.startsWith('temp')) {
      const num = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(num) ? num.toFixed(2) : 'nan';
    }
    if (column === 'valve') {
      const num = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(num) ? String(Math.round(num)) : '0';
    }
    if (column === 'mode') {
      const text = typeof value === 'string' ? value : String(value || '');
      return text.slice(0, 1);
    }
    if (column.startsWith('pump_') || column.startsWith('fluid_') || column.startsWith('scale_')) {
      const digits = LOG_FIELD_DIGITS.get(column) ?? 3;
      const num = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(num) ? num.toFixed(digits) : 'nan';
    }
    if (typeof value === 'number') {
      return Number.isFinite(value) ? value.toString() : 'nan';
    }
    if (typeof value === 'string') {
      return value;
    }
    return '';
  }

  function updateSensorStats() {
    if (!latestSnapshot) {
      return;
    }
    const { temps, sensorCount } = latestSnapshot;
    if (!sensorValuesEl) {
      return;
    }
    const indices = visibleSensorIndices(sensorCount);

    let validNow = 0;
    let pressureValid = 0;
    const validLabels = [];
    const tempChips = [];
    const pressureChips = [];
    const pump = latestSnapshot.pump && typeof latestSnapshot.pump === 'object' ? latestSnapshot.pump : null;

    for (const i of indices) {
      const value = temps[i];
      const finite = Number.isFinite(value);
      const classes = ['sensor-chip'];
      if (!finite) {
        classes.push('inactive');
      }
      const displayValue = finite ? formatThermocoupleTemperature(value) : '—';
      tempChips.push(`<div class="${classes.join(' ')}">${sensorLongName(i)}: ${displayValue}</div>`);
      if (finite) {
        validNow += 1;
        validLabels.push(sensorShortName(i));
      }
    }

    for (const pressureMeta of PRESSURE_SENSOR_METADATA) {
      const value = pump ? finiteNumber(pump[pressureMeta.key]) : NaN;
      const finite = Number.isFinite(value);
      if (finite) {
        pressureValid += 1;
      }
      pressureChips.push(
        `<div class="sensor-chip pressure-chip${finite ? '' : ' inactive'}">${pressureMeta.tag} (${pressureMeta.label}): ${
          finite ? `${value.toFixed(3)} bar` : '—'
        }</div>`,
      );
    }

    const rows = [];
    if (tempChips.length) {
      rows.push(`<div class="sensor-chip-row">${tempChips.join('')}</div>`);
    }
    if (pressureChips.length) {
      rows.push(`<div class="sensor-chip-row">${pressureChips.join('')}</div>`);
    }

    sensorValuesEl.innerHTML = rows.length ? rows.join('') : '<p class="muted">No connected sensor telemetry yet.</p>';
    sensorCountEl.textContent = `Temps: ${indices.length} • Pressures: ${PRESSURE_SENSOR_METADATA.length}`;
    validCountEl.textContent = `Temp valid: ${validNow} • Pressure valid: ${pressureValid}`;
    validListEl.textContent = validLabels.length ? `Valid temps: ${validLabels.join(', ')}` : 'Valid temps: —';
    if (latestSnapshot) {
      updateFluidTelemetry(latestSnapshot.fluid, latestSnapshot.pump);
    }
    scheduleChartHeightUpdate();
  }

  function setCommandStatus(text, tone = 'normal') {
    if (!commandStatusEl) {
      return;
    }
    commandStatusEl.textContent = text;
    commandStatusEl.dataset.tone = tone || 'normal';
  }

  function renderMetric(mainEl, subEl, value, digits = 2, pctValue = null) {
    if (mainEl) {
      mainEl.textContent = Number.isFinite(value) ? value.toFixed(digits) : '—';
    }
    if (subEl) {
      subEl.textContent = Number.isFinite(pctValue) ? `${pctValue.toFixed(1)} %` : '';
    }
  }

  function renderHeaterState(el, onValue) {
    if (!el) {
      return;
    }
    if (onValue === null || onValue === undefined) {
      el.textContent = '—';
      el.classList.remove('valve-open');
      el.classList.add('valve-closed');
      return;
    }
    const active = Boolean(onValue);
    el.textContent = active ? 'On' : 'Off';
    el.classList.toggle('valve-open', active);
    el.classList.toggle('valve-closed', !active);
  }

  function coerceOnOff(value) {
    if (value === null || value === undefined) {
      return null;
    }
    if (typeof value === 'boolean') {
      return value;
    }
    if (typeof value === 'number') {
      return Number.isNaN(value) ? null : value !== 0;
    }
    if (typeof value === 'string') {
      const norm = value.trim().toLowerCase();
      if (!norm) {
        return null;
      }
      if (norm === 'on' || norm === '1' || norm === 'true') {
        return true;
      }
      if (norm === 'off' || norm === '0' || norm === 'false') {
        return false;
      }
    }
    return null;
  }

  function finiteNumber(value) {
    if (typeof value === 'number') {
      return Number.isFinite(value) ? value : NaN;
    }
    const num = Number(value);
    return Number.isFinite(num) ? num : NaN;
  }

  function formatNumber(value, digits = 2, suffix = '') {
    return Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '—';
  }

  function convertFlowTemperature(rawValue) {
    const raw = finiteNumber(rawValue);
    if (!Number.isFinite(raw)) {
      return { celsius: NaN, main: '—' };
    }

    if (FLOW_TEMPERATURE_SOURCE_UNIT === 'fahrenheit') {
      const celsius = ((raw - 32) * 5) / 9;
      return {
        celsius,
        main: `${celsius.toFixed(2)} °C`,
      };
    }
    if (FLOW_TEMPERATURE_SOURCE_UNIT === 'kelvin') {
      const celsius = raw - 273.15;
      return {
        celsius,
        main: `${celsius.toFixed(2)} °C`,
      };
    }
    return {
      celsius: raw,
      main: `${raw.toFixed(2)} °C`,
    };
  }

  function buildFluidTelemetryModel(fluidData, pumpData) {
    const fluid = fluidData && typeof fluidData === 'object' ? fluidData : null;
    const pump = pumpData && typeof pumpData === 'object' ? pumpData : null;
    const concentrationPct =
      fluid && Number.isFinite(fluid.concentration_pct)
        ? fluid.concentration_pct
        : FLUID_REFERENCE.concentrationPct;
    const temperatureRaw = fluid ? finiteNumber(fluid.temperature_raw) : NaN;
    const temperatureDirectC = fluid ? finiteNumber(fluid.temperature_c) : NaN;
    const temperature = Number.isFinite(temperatureDirectC)
      ? { celsius: temperatureDirectC, main: `${temperatureDirectC.toFixed(2)} °C` }
      : convertFlowTemperature(temperatureRaw);
    const beforeBar = pump ? finiteNumber(pump.pressure_before_bar_abs) : NaN;
    const afterBar = pump ? finiteNumber(pump.pressure_after_bar_abs) : NaN;
    const tankPressureBar = pump ? finiteNumber(pump.pressure_tank_bar_abs) : NaN;
    const deltaPBar =
      Number.isFinite(beforeBar) && Number.isFinite(afterBar) ? afterBar - beforeBar : NaN;

    return {
      name:
        fluid && typeof fluid.name === 'string' && fluid.name.trim()
          ? fluid.name.trim()
          : FLUID_REFERENCE.name,
      concentration_pct: concentrationPct,
      meter_valid: fluid && coerceOnOff(fluid.meter_valid) === true ? 1 : 0,
      flow_velocity_mps: fluid ? finiteNumber(fluid.flow_velocity_mps) : NaN,
      volume_flow_m3s: fluid ? finiteNumber(fluid.volume_flow_m3s) : NaN,
      mass_flow_kgs: fluid ? finiteNumber(fluid.mass_flow_kgs) : NaN,
      temperature_raw: temperatureRaw,
      temperature_c: temperature.celsius,
      density_kg_m3: fluid ? finiteNumber(fluid.density_kg_m3) : NaN,
      delta_p_bar: deltaPBar,
      tank_pressure_bar_abs: tankPressureBar,
    };
  }

  function buildPumpSafetyModel(safetyData, pumpData) {
    const safety = safetyData && typeof safetyData === 'object' ? safetyData : null;
    const laws = safety && safety.laws && typeof safety.laws === 'object' ? safety.laws : null;
    const rawLaw =
      laws && laws[PUMP_SAFETY_LAW_KEY] && typeof laws[PUMP_SAFETY_LAW_KEY] === 'object'
        ? laws[PUMP_SAFETY_LAW_KEY]
        : null;
    const pump = pumpData && typeof pumpData === 'object' ? pumpData : null;
    const beforeBar = pump ? finiteNumber(pump.pressure_before_bar_abs) : NaN;
    const afterBar = pump ? finiteNumber(pump.pressure_after_bar_abs) : NaN;
    const deltaPBar =
      Number.isFinite(beforeBar) && Number.isFinite(afterBar) ? afterBar - beforeBar : NaN;
    const lawLimitBar = rawLaw ? finiteNumber(rawLaw.limit_bar) : NaN;
    const lawValueBar = rawLaw ? finiteNumber(rawLaw.value_bar) : NaN;

    return {
      available: Boolean(safety),
      emergencyStop: safety ? coerceOnOff(safety.emergency_stop) === true : false,
      resetRequired: safety ? coerceOnOff(safety.reset_required) === true : false,
      activeReason:
        safety && typeof safety.active_reason === 'string' ? safety.active_reason.trim() : '',
      message:
        safety && typeof safety.message === 'string' && safety.message.trim()
          ? safety.message.trim()
          : '',
      lawKey: PUMP_SAFETY_LAW_KEY,
      lawLabel:
        rawLaw && typeof rawLaw.label === 'string' && rawLaw.label.trim()
          ? rawLaw.label.trim()
          : PUMP_SAFETY_LAW_LABEL,
      lawActive: rawLaw ? coerceOnOff(rawLaw.active) === true : false,
      lawTripped: rawLaw ? coerceOnOff(rawLaw.tripped) === true : false,
      limitBar: Number.isFinite(lawLimitBar) ? lawLimitBar : PUMP_DELTA_P_ESTOP_LIMIT_BAR,
      valueBar: Number.isFinite(lawValueBar) ? lawValueBar : deltaPBar,
      deltaPBar,
    };
  }

  function currentMaxPumpPct() {
    if (overspeedEnabled) {
      return PUMP_MAX_CMD_PCT;
    }
    if (!Number.isFinite(pumpMaxFreqHz) || pumpMaxFreqHz <= 0) {
      return PUMP_MAX_CMD_PCT;
    }
    const safePct = (Math.min(PUMP_SAFE_MAX_HZ, pumpMaxFreqHz) / pumpMaxFreqHz) * 100;
    return Math.max(0, Math.min(PUMP_MAX_CMD_PCT, safePct));
  }

  function clampPumpPct(value) {
    if (!Number.isFinite(value)) {
      return 0;
    }
    return Math.min(Math.max(value, 0), currentMaxPumpPct());
  }

  function pumpHzFromPct(pct, maxFreq = pumpMaxFreqHz) {
    if (!Number.isFinite(pct) || !Number.isFinite(maxFreq) || maxFreq <= 0) {
      return NaN;
    }
    return (pct / 100) * maxFreq;
  }

  function applyOverspeedToggle(enabled) {
    overspeedEnabled = Boolean(enabled);
    syncPumpInputs(lastPumpCmdPct, { force: true });
    if (pumpOverspeedToggle) {
      pumpOverspeedToggle.checked = overspeedEnabled;
    }
  }

  function formatPumpPctText(pct) {
    return clampPumpPct(pct).toFixed(2);
  }

  function parsePumpPctInput(value) {
    if (value === null || value === undefined) {
      return NaN;
    }
    const trimmed = String(value).trim();
    if (!trimmed) {
      return NaN;
    }
    return Number.parseFloat(trimmed);
  }

  function syncPumpInputs(pct, { force = false, preserveTypedInput = false } = {}) {
    const clamped = clampPumpPct(pct);
    lastPumpCmdPct = clamped;
    const asText = formatPumpPctText(clamped);
    if (pumpCmdInput) {
      pumpCmdInput.max = currentMaxPumpPct().toFixed(1);
      const shouldWriteInput = (!userPumpDirty || force) && !(preserveTypedInput && document.activeElement === pumpCmdInput);
      if (shouldWriteInput) {
        pumpCmdInput.value = asText;
      }
    }
    if (pumpCmdSlider) {
      pumpCmdSlider.max = currentMaxPumpPct().toFixed(1);
      pumpCmdSlider.value = asText;
    }
  }

  function normalizePumpTextInput({ fallbackPct = lastPumpCmdPct } = {}) {
    if (!pumpCmdInput) {
      return clampPumpPct(fallbackPct);
    }
    const parsed = parsePumpPctInput(pumpCmdInput.value);
    const normalized = Number.isFinite(parsed) ? clampPumpPct(parsed) : clampPumpPct(fallbackPct);
    syncPumpInputs(normalized, { force: true });
    return normalized;
  }

  function updatePumpActionButton(buttonEl, running) {
    if (!buttonEl) {
      return;
    }
    if (pumpSafetyState.resetRequired) {
      buttonEl.textContent = 'Reset Emergency Stop';
      buttonEl.classList.add('danger');
      buttonEl.classList.remove('primary');
      buttonEl.setAttribute('aria-label', 'Reset emergency stop');
      buttonEl.title = 'Reset emergency stop once the safety condition is cleared';
      return;
    }
    const startLabel = `Start Pump (${PUMP_DEFAULT_START_PCT.toFixed(0)}%)`;
    buttonEl.textContent = running ? 'Stop Pump' : startLabel;
    buttonEl.classList.toggle('danger', running);
    buttonEl.classList.toggle('primary', !running);
    buttonEl.setAttribute(
      'aria-label',
      running ? 'Stop pump' : `Start pump at ${PUMP_DEFAULT_START_PCT.toFixed(0)} percent`,
    );
    buttonEl.title = running
      ? 'Stop pump'
      : `Start pump at ${PUMP_DEFAULT_START_PCT.toFixed(0)}% command`;
  }

  function updatePumpActionButtons() {
    updatePumpActionButton(globalPumpStopButton, pumpRunning);
    updatePumpActionButton(pumpStopButton, pumpRunning);
  }

  function updatePumpCommandAvailability() {
    const locked = pumpSafetyState.resetRequired;
    [pumpCmdInput, pumpCmdSlider, pumpOverspeedToggle, pumpSpeedSubmitButton].forEach((el) => {
      if (el) {
        el.disabled = locked;
      }
    });
  }

  function updatePumpSafetyStatus() {
    const limitDigits = Number.isInteger(pumpSafetyState.limitBar) ? 0 : 3;
    const limitText = `${pumpSafetyState.limitBar.toFixed(limitDigits)} bar`;
    const valueText = formatPressureValue(pumpSafetyState.valueBar);
    if (pumpSafetyStatusEl) {
      if (pumpSafetyState.resetRequired) {
        pumpSafetyStatusEl.textContent = `Emergency stop latched. ${pumpSafetyState.lawLabel} measured ${valueText} against a ${limitText} limit. Press Reset Emergency Stop once the condition is clear.`;
        setTone(pumpSafetyStatusEl, 'error');
      } else if (pumpSafetyState.available) {
        pumpSafetyStatusEl.textContent = `Safety interlocks clear. Pump ΔP trip limit: ${limitText}.`;
        setTone(pumpSafetyStatusEl, 'success');
      } else {
        pumpSafetyStatusEl.textContent = `Pump safety telemetry unavailable. Configured ΔP trip limit: ${limitText}.`;
        setTone(pumpSafetyStatusEl);
      }
    }
    if (overviewPumpDeltaPSubEl) {
      if (pumpSafetyState.resetRequired) {
        overviewPumpDeltaPSubEl.textContent = 'Emergency stop latched';
        setTone(overviewPumpDeltaPSubEl, 'error');
      } else {
        overviewPumpDeltaPSubEl.textContent = '';
        setTone(overviewPumpDeltaPSubEl);
      }
    }
    if (overviewPumpSpeedSubEl) {
      overviewPumpSpeedSubEl.textContent = pumpSafetyState.resetRequired
        ? 'Emergency stop latched'
        : '';
      setTone(overviewPumpSpeedSubEl, pumpSafetyState.resetRequired ? 'error' : '');
    }
    if (overviewPumpSpeedEl) {
      setTone(overviewPumpSpeedEl, pumpSafetyState.resetRequired ? 'error' : '');
    }
    updatePumpCommandAvailability();
  }

  function issuePumpStart(defaultPct = PUMP_DEFAULT_START_PCT) {
    if (pumpSafetyState.resetRequired) {
      setCommandStatus('Emergency stop latched. Reset it before restarting the pump.', 'error');
      return;
    }
    const targetPct = clampPumpPct(defaultPct);
    userPumpDirty = false;
    pumpRunning = targetPct > 0;
    syncPumpInputs(targetPct, { force: true });
    updatePumpActionButtons();
    sendCommand(`PUMP ${targetPct.toFixed(2)}`);
    setCommandStatus(`Pump start issued (${targetPct.toFixed(2)}%)`, 'info');
  }

  function issuePumpStop() {
    userPumpDirty = false;
    pumpRunning = false;
    syncPumpInputs(0, { force: true });
    updatePumpActionButtons();
    sendCommand('PUMP 0');
    setCommandStatus('Pump stop issued (0%)', 'info');
  }

  async function issueEmergencyStopReset() {
    try {
      setCommandStatus('Resetting emergency stop…', 'info');
      await apiJson('/api/command', {
        method: 'POST',
        body: JSON.stringify({ cmd: 'ESTOP RESET' }),
      });
      setCommandStatus('Emergency-stop reset requested', 'info');
    } catch (err) {
      console.error('Emergency-stop reset failed', err);
      setCommandStatus(`Emergency-stop reset failed: ${err.message}`, 'error');
    }
  }

  async function apiJson(path, options = {}) {
    const headers = options.headers ? { ...options.headers } : {};
    if (authHeaderValue) {
      headers.Authorization = authHeaderValue;
    }
    if (!headers['Content-Type'] && options.body !== undefined && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, { ...options, headers });
    if (!response.ok) {
      let detail = '';
      try {
        detail = await response.text();
      } catch (err) {
        detail = response.statusText;
      }
      throw new Error(detail || response.statusText);
    }
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return response.json();
    }
    return {};
  }

  async function refreshLoggingStatus() {
    try {
      const data = await apiJson('/api/logging/status', { method: 'GET' });
      serverLogInfo = {
        active: Boolean(data.active),
        filename: data.filename || null,
        path: data.path || null,
        rows: typeof data.rows === 'number' ? data.rows : 0,
      };
      loggingEnabled = serverLogInfo.active;
      if (serverLogInfo.active) {
        loggingRows = [];
      }
      updateLoggingStatusLabel();
      updateLoggingButtonState();
    } catch (err) {
      console.warn('Logging status fetch failed', err);
      updateLoggingStatusLabel();
      updateLoggingButtonState();
    }
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const base = `${protocol}://${window.location.host}/ws`;
    const url = tokenParam ? `${base}?token=${encodeURIComponent(tokenParam)}` : base;

    setConnectionStatus('connecting…', 'info');
    ws = new WebSocket(url);

    ws.addEventListener('open', () => {
      setConnectionStatus('telemetry connected', 'success');
      reconnectDelay = 1000;
    });

    ws.addEventListener('message', (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type !== 'telemetry') {
          return;
        }
        setConnectionStatus('receiving telemetry', 'success');
        handleTelemetry(payload);
      } catch (err) {
        console.error('Failed to parse telemetry', err);
      }
    });

    ws.addEventListener('close', () => {
      setConnectionStatus('connection lost, retrying…', 'warn');
      scheduleReconnect();
    });

    ws.addEventListener('error', (err) => {
      console.error('WebSocket error', err);
      ws.close();
    });
  }

  function scheduleReconnect() {
    if (ws) {
      ws = null;
    }
    const delay = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    setTimeout(() => {
      connectWebSocket();
    }, delay);
  }

  function pushSeries(series, point) {
    series.push(point);
    if (series.length > MAX_POINTS) {
      series.shift();
    }
  }

  function shiftSeriesLeft(series, deltaMinutes) {
    if (!Array.isArray(series) || !Number.isFinite(deltaMinutes) || deltaMinutes <= 0) {
      return;
    }
    for (const point of series) {
      if (point && typeof point.x === 'number') {
        point.x -= deltaMinutes;
      }
    }
    let removeCount = 0;
    for (let i = 0; i < series.length; i += 1) {
      const point = series[i];
      if (!point || typeof point.x !== 'number') {
        continue;
      }
      if (point.x < 0) {
        removeCount += 1;
      } else {
        break;
      }
    }
    if (removeCount > 0) {
      series.splice(0, removeCount);
    }
  }

  function shiftAllSeriesLeft(deltaMinutes) {
    if (!Number.isFinite(deltaMinutes) || deltaMinutes <= 0) {
      return;
    }
    for (let i = 0; i < sensorSeries.length; i += 1) {
      shiftSeriesLeft(sensorSeries[i], deltaMinutes);
      sensorDatasets[i].data = sensorSeries[i];
    }
    shiftSeriesLeft(setpointSeries, deltaMinutes);
    setpointDataset.data = setpointSeries;
    for (let i = 0; i < pressureSeries.length; i += 1) {
      shiftSeriesLeft(pressureSeries[i], deltaMinutes);
      pressureDatasets[i].data = pressureSeries[i];
    }
  }

  function updateChartRanges() {
    if (chart) {
      chart.options.scales.x.min = 0;
      chart.options.scales.x.max = WINDOW_MINUTES;
    }
    if (pressureChart) {
      pressureChart.options.scales.x.min = 0;
      pressureChart.options.scales.x.max = WINDOW_MINUTES;
    }
  }

  function updatePumpTelemetry(pumpData, safetyData = pumpSafetyState) {
    const pump = pumpData && typeof pumpData === 'object' ? pumpData : null;
    const safetyState =
      safetyData && typeof safetyData === 'object' ? safetyData : buildPumpSafetyModel(null, pump);
    if (pump && Number.isFinite(pump.max_freq_hz) && pump.max_freq_hz > 0) {
      pumpMaxFreqHz = pump.max_freq_hz;
    }

    const cmdPct = pump && Number.isFinite(pump.cmd_pct) ? clampPumpPct(pump.cmd_pct) : lastPumpCmdPct;
    const cmdHz =
      pump && Number.isFinite(pump.cmd_hz) ? pump.cmd_hz : pumpHzFromPct(cmdPct, pumpMaxFreqHz);
    if (safetyState.resetRequired) {
      userPumpDirty = false;
      syncPumpInputs(cmdPct, { force: true });
    } else if (!userPumpDirty) {
      syncPumpInputs(cmdPct, { force: false });
    }

    const freqHz = pump && Number.isFinite(pump.freq_hz) ? pump.freq_hz : null;
    const freqPct =
      Number.isFinite(freqHz) && Number.isFinite(pumpMaxFreqHz) && pumpMaxFreqHz > 0
        ? (freqHz / pumpMaxFreqHz) * 100
        : null;

    const currentA = pump && Number.isFinite(pump.output_current_a) ? pump.output_current_a : null;
    const currentPct =
      Number.isFinite(currentA) && PUMP_NAMEPLATE_CURRENT_A > 0
        ? (currentA / PUMP_NAMEPLATE_CURRENT_A) * 100
        : null;
    const voltageV = pump && Number.isFinite(pump.output_voltage_v) ? pump.output_voltage_v : null;
    const voltagePct =
      Number.isFinite(voltageV) && PUMP_BASE_VOLTAGE_V > 0
        ? (voltageV / PUMP_BASE_VOLTAGE_V) * 100
        : null;
    const powerW =
      pump && Number.isFinite(pump.input_power_w)
        ? pump.input_power_w
        : pump && Number.isFinite(pump.input_power_kw)
        ? pump.input_power_kw * 1000
        : null;
    const powerPct =
      Number.isFinite(powerW) && PUMP_EST_RATED_INPUT_W > 0
        ? (powerW / PUMP_EST_RATED_INPUT_W) * 100
        : null;
    const rotationSpeedRpm =
      pump && Number.isFinite(pump.rotation_speed_rpm) ? pump.rotation_speed_rpm : null;

    const overviewHz = Number.isFinite(freqHz) ? freqHz : cmdHz;

    if (overviewPumpSpeedEl) {
      overviewPumpSpeedEl.textContent = Number.isFinite(overviewHz)
        ? `${overviewHz.toFixed(2)} Hz`
        : '—';
    }
    const running = safetyState.resetRequired
      ? false
      : Number.isFinite(freqHz)
      ? freqHz > 0.2
      : cmdPct > 0.2;
    pumpRunning = running;
    if (pumpRunStateEl) {
      if (safetyState.resetRequired) {
        pumpRunStateEl.textContent = 'Emergency stop';
        pumpRunStateEl.classList.remove('valve-open', 'valve-closed');
        pumpRunStateEl.classList.add('state-alert');
      } else {
        pumpRunStateEl.textContent = running ? 'Running' : 'Stopped';
        pumpRunStateEl.classList.toggle('valve-open', running);
        pumpRunStateEl.classList.toggle('valve-closed', !running);
        pumpRunStateEl.classList.remove('state-alert');
      }
    }
    updatePumpSafetyStatus();
    updatePumpActionButtons();
    if (pumpCmdHzEl) {
      pumpCmdHzEl.textContent = Number.isFinite(cmdHz) ? `${cmdHz.toFixed(2)} Hz` : '—';
    }
    if (pumpCmdRpmEl) {
      pumpCmdRpmEl.textContent = overspeedEnabled ? '0 to 71.7 Hz' : '0 to 60.0 Hz';
    }
    if (pumpCmdFlowEl) {
      pumpCmdFlowEl.textContent = overspeedEnabled
        ? 'Overspeed enabled for commands above 60 Hz'
        : 'Nominal operating range with overspeed locked out';
    }

    renderMetric(vfdFrequencyEl, vfdFrequencyPctEl, freqHz, 2, freqPct);
    renderMetric(vfdCurrentEl, vfdCurrentPctEl, currentA, 2, currentPct);
    renderMetric(vfdVoltageEl, vfdVoltagePctEl, voltageV, 1, voltagePct);
    renderMetric(vfdPowerEl, vfdPowerPctEl, powerW, 0, powerPct);
    renderMetric(vfdSpeedEl, vfdSpeedSubEl, rotationSpeedRpm, 0, null);
    if (vfdSpeedSubEl) {
      vfdSpeedSubEl.textContent = 'estimated from output Hz';
    }
    if (vfdPowerUnitEl) {
      vfdPowerUnitEl.textContent = 'W';
    }

    const pressureError =
      pump && Number.isFinite(pump.pressure_error_bar) ? pump.pressure_error_bar : 0.05;
    const pressureUnitText = `(±${pressureError.toFixed(2)} bar)`;
    if (pumpPressureBeforeUnitEl) {
      pumpPressureBeforeUnitEl.textContent = pressureUnitText;
    }
    if (pumpPressureAfterUnitEl) {
      pumpPressureAfterUnitEl.textContent = pressureUnitText;
    }

    const beforeBar =
      pump && Number.isFinite(pump.pressure_before_bar_abs) ? pump.pressure_before_bar_abs : null;
    const afterBar =
      pump && Number.isFinite(pump.pressure_after_bar_abs) ? pump.pressure_after_bar_abs : null;
    const deltaPBar =
      Number.isFinite(beforeBar) && Number.isFinite(afterBar) ? afterBar - beforeBar : null;

    if (pumpPressureBeforeEl) {
      pumpPressureBeforeEl.textContent = Number.isFinite(beforeBar) ? beforeBar.toFixed(2) : '—';
    }
    if (pumpPressureAfterEl) {
      const value = Number.isFinite(afterBar) ? afterBar : null;
      pumpPressureAfterEl.textContent = Number.isFinite(value) ? value.toFixed(2) : '—';
    }
    if (overviewPumpDeltaPEl) {
      overviewPumpDeltaPEl.textContent = Number.isFinite(deltaPBar) ? `${deltaPBar.toFixed(3)} bar` : '—';
    }
    if (fluidTankPressureSubEl) {
      fluidTankPressureSubEl.textContent = pressureUnitText;
    }
  }

  function updateFluidTelemetry(fluidData, pumpData) {
    const fluidModel = buildFluidTelemetryModel(fluidData, pumpData);
    const pump = pumpData && typeof pumpData === 'object' ? pumpData : null;
    const volumeFlowLMin = Number.isFinite(fluidModel.volume_flow_m3s)
      ? fluidModel.volume_flow_m3s * 60000
      : NaN;
    const massFlowKgMin = Number.isFinite(fluidModel.mass_flow_kgs)
      ? fluidModel.mass_flow_kgs * 60
      : NaN;
    const pressureError =
      pump && Number.isFinite(pump.pressure_error_bar) ? pump.pressure_error_bar : 0.05;

    if (fluidNameEl) {
      fluidNameEl.textContent = fluidModel.name;
    }
    if (fluidConcentrationEl) {
      const digits = Number.isInteger(fluidModel.concentration_pct) ? 0 : 1;
      fluidConcentrationEl.textContent = `${fluidModel.concentration_pct.toFixed(digits)}% composition`;
    }
    if (fluidTankPressureEl) {
      fluidTankPressureEl.textContent = formatNumber(fluidModel.tank_pressure_bar_abs, 3, ' bar');
    }
    if (fluidTankPressureSubEl) {
      fluidTankPressureSubEl.textContent = `(±${pressureError.toFixed(2)} bar)`;
    }

    if (fluidFlowVelocityEl) {
      fluidFlowVelocityEl.textContent = formatNumber(fluidModel.flow_velocity_mps, 3, ' m/s');
    }

    if (fluidVolumeFlowEl) {
      fluidVolumeFlowEl.textContent = formatNumber(volumeFlowLMin, 3, ' L/min');
    }

    if (fluidMassFlowEl) {
      fluidMassFlowEl.textContent = formatNumber(massFlowKgMin, 3, ' kg/min');
    }

    if (fluidTemperatureEl) {
      fluidTemperatureEl.textContent = formatNumber(fluidModel.temperature_c, 2, ' °C');
    }

    if (fluidDensityEl) {
      fluidDensityEl.textContent = formatNumber(fluidModel.density_kg_m3, 0, ' kg/m³');
    }

    if (fluidTempRiseEl) {
      const tankInletTemp =
        latestSnapshot && Array.isArray(latestSnapshot.temps)
          ? finiteNumber(latestSnapshot.temps[TTI_SENSOR_INDEX])
          : NaN;
      const tankOutletTemp =
        latestSnapshot && Array.isArray(latestSnapshot.temps)
          ? finiteNumber(latestSnapshot.temps[TTO_SENSOR_INDEX])
          : NaN;
      if (Number.isFinite(tankOutletTemp) && Number.isFinite(tankInletTemp)) {
        const delta = tankOutletTemp - tankInletTemp;
        fluidTempRiseEl.textContent = `${delta.toFixed(2)} °C`;
      } else {
        fluidTempRiseEl.textContent = 'Awaiting TTO and TTI temperatures';
      }
    }

    if (fluidViscosityEl) {
      fluidViscosityEl.textContent = 'Awaiting calibrated hydraulic model';
    }

    if (fluidMixingEfficiencyEl) {
      fluidMixingEfficiencyEl.textContent = 'Reserved';
    }
  }

  function setScaleTareStatus(text, tone = '') {
    if (!scaleTareStatusEl) {
      return;
    }
    scaleTareStatusEl.textContent = text;
    setTone(scaleTareStatusEl, tone);
  }

  function syncScaleTareInput({ force = false } = {}) {
    if (!scaleTareInput) {
      return;
    }
    if (force || document.activeElement !== scaleTareInput) {
      scaleTareInput.value = scaleTareKg.toFixed(3);
    }
  }

  async function refreshScaleTare() {
    try {
      const data = await apiJson('/api/scale/tare', { method: 'GET' });
      const nextTareKg = finiteNumber(data.tare_kg);
      if (Number.isFinite(nextTareKg)) {
        scaleTareKg = nextTareKg;
        syncScaleTareInput({ force: true });
        setScaleTareStatus(`Tare set to ${scaleTareKg.toFixed(3)} kg`, 'success');
      }
    } catch (err) {
      console.warn('Scale tare fetch failed', err);
      setScaleTareStatus('Tare status unavailable', 'warn');
    }
  }

  function updateScaleTelemetry(scaleData) {
    const scale = scaleData && typeof scaleData === 'object' ? scaleData : null;
    const weightKg = scale ? finiteNumber(scale.weight_kg) : NaN;
    const tareKg = scale ? finiteNumber(scale.tare_kg) : NaN;
    const stale = scale ? coerceOnOff(scale.stale) === true : false;
    const stable = scale ? coerceOnOff(scale.stable) : null;

    if (Number.isFinite(tareKg)) {
      scaleTareKg = tareKg;
      syncScaleTareInput();
      if (scaleTareStatusEl && !scaleTareStatusEl.textContent.trim()) {
        setScaleTareStatus(`Tare set to ${scaleTareKg.toFixed(3)} kg`, 'success');
      }
    }

    if (overviewScaleWeightEl) {
      overviewScaleWeightEl.textContent = Number.isFinite(weightKg)
        ? `${weightKg.toFixed(1)} kg`
        : '—';
      setTone(overviewScaleWeightEl, stale ? 'warn' : '');
    }

    if (overviewScaleStatusEl) {
      const parts = [];
      if (stable !== null) {
        parts.push(stable ? 'stable' : 'moving');
      }
      overviewScaleStatusEl.textContent = parts.length ? parts.join(' • ') : 'Awaiting scale';
      setTone(overviewScaleStatusEl, stale ? 'warn' : '');
    }
  }

  function handleTelemetry(data) {
    const tempsRaw = Array.isArray(data.temps)
      ? data.temps
      : typeof data.tC === 'number'
      ? [data.tC]
      : [];
    const sensorCount = tempsRaw.length ? Math.min(tempsRaw.length, MAX_SENSORS) : 1;
    const temps = tempsRaw.length
      ? tempsRaw.slice(0, MAX_SENSORS)
      : [Number.isFinite(data.tC) ? data.tC : NaN];

    while (temps.length < MAX_SENSORS) {
      temps.push(NaN);
    }

    const ts = typeof data.t === 'number' ? data.t : Date.now() / 1000;
    if (startEpochSec === null) {
      startEpochSec = ts;
    }
    let tMin = (ts - startEpochSec) / 60;
    const visibleIndices = new Set(visibleSensorIndices(sensorCount));
    const control = data && typeof data.control === 'object' ? data.control : null;
    const telemetryHfeGoal = control
      ? finiteNumber(control.hfe_goal_c ?? control.setpoint_c)
      : NaN;
    const telemetryHxLimit = control
      ? finiteNumber(
          control.hx_limit_c ??
            control.thi_limit_c ??
            (Number.isFinite(finiteNumber(control.setpoint_c)) &&
            Number.isFinite(finiteNumber(control.thi_guard_offset_c))
              ? finiteNumber(control.setpoint_c) - finiteNumber(control.thi_guard_offset_c)
              : NaN),
        )
      : NaN;
    const telemetryHysteresis = control ? finiteNumber(control.ln_hysteresis_c) : NaN;
    const telemetryHxApproach = control ? finiteNumber(control.hx_approach_c) : NaN;

    if (
      canUseTelemetryAutoTarget('hfeGoal', telemetryHfeGoal, currentHfeGoalC) &&
      telemetryHfeGoal !== currentHfeGoalC
    ) {
      currentHfeGoalC = telemetryHfeGoal;
      setpointDataset.label = hfeGoalLabel();
      if (hfeGoalInput && document.activeElement !== hfeGoalInput) {
        hfeGoalInput.value = currentHfeGoalC.toFixed(2);
      }
    }
    if (
      canUseTelemetryAutoTarget('hxLimit', telemetryHxLimit, currentHxLimitC) &&
      telemetryHxLimit !== currentHxLimitC
    ) {
      currentHxLimitC = telemetryHxLimit;
      if (hxLimitInput && document.activeElement !== hxLimitInput) {
        hxLimitInput.value = currentHxLimitC.toFixed(2);
      }
    }
    if (
      canUseTelemetryAutoTarget('hysteresis', telemetryHysteresis, currentAutoHysteresisC) &&
      telemetryHysteresis !== currentAutoHysteresisC
    ) {
      currentAutoHysteresisC = telemetryHysteresis;
      if (autoHysteresisInput && document.activeElement !== autoHysteresisInput) {
        autoHysteresisInput.value = currentAutoHysteresisC.toFixed(2);
      }
    }
    if (
      canUseTelemetryAutoTarget('hxApproach', telemetryHxApproach, currentHxApproachC) &&
      telemetryHxApproach !== currentHxApproachC
    ) {
      currentHxApproachC = telemetryHxApproach;
      if (hxApproachInput && document.activeElement !== hxApproachInput) {
        hxApproachInput.value = currentHxApproachC.toFixed(2);
      }
    }

    for (let i = 0; i < MAX_SENSORS; i += 1) {
      const value = Number.isFinite(temps[i]) ? temps[i] : null;
      pushSeries(sensorSeries[i], value === null ? { x: tMin, y: null } : { x: tMin, y: value });
      sensorDatasets[i].data = sensorSeries[i];
      sensorDatasets[i].hidden = !visibleIndices.has(i);
    }

    pushSeries(setpointSeries, { x: tMin, y: currentHfeGoalC });
    setpointDataset.data = setpointSeries;

    if (tMin > WINDOW_MINUTES) {
      const overflow = tMin - WINDOW_MINUTES;
      shiftAllSeriesLeft(overflow);
      startEpochSec += overflow * 60;
      tMin -= overflow;
    }

    const pump = data && typeof data.pump === 'object' ? data.pump : null;
    const safety = data && typeof data.safety === 'object' ? data.safety : null;
    const fluid = data && typeof data.fluid === 'object' ? data.fluid : null;
    const scale = data && typeof data.scale === 'object' ? data.scale : null;
    const pressureValues = [
      pump && Number.isFinite(pump.pressure_before_bar_abs) ? pump.pressure_before_bar_abs : null,
      pump && Number.isFinite(pump.pressure_after_bar_abs) ? pump.pressure_after_bar_abs : null,
      pump && Number.isFinite(pump.pressure_tank_bar_abs) ? pump.pressure_tank_bar_abs : null,
    ];

    for (let i = 0; i < pressureSeries.length; i += 1) {
      const value = pressureValues[i];
      pushSeries(pressureSeries[i], value === null ? { x: tMin, y: null } : { x: tMin, y: value });
      pressureDatasets[i].data = pressureSeries[i];
    }

    const valve = Number.isFinite(data.valve) ? Number(data.valve) : 0;

    const valveOpen = Boolean(valve);
    const valveLabel = valveOpen ? 'Open' : 'Closed';
    valveStateEl.textContent = valveLabel;
    valveStateEl.classList.toggle('valve-open', valveOpen);
    valveStateEl.classList.toggle('valve-closed', !valveOpen);

    const modeCharRaw = typeof data.mode === 'string' ? data.mode : '';
    const modeChar = modeCharRaw ? modeCharRaw.charAt(0).toUpperCase() : '';
    let modeText;
    if (modeChar === 'A') {
      modeText = 'Auto';
    } else if (modeChar === 'O') {
      modeText = 'Forced open';
    } else if (modeChar === 'C') {
      modeText = 'Forced close';
    } else {
      modeText = '—';
    }
    modeStateEl.textContent = `Mode: ${modeText}`;
    if (overviewValveEl) {
      overviewValveEl.textContent = valveLabel;
    }

    const heaters = data && typeof data.heaters === 'object' ? data.heaters : null;
    const bottomOn = heaters ? coerceOnOff(heaters.bottom) : null;
    const exhaustOn = heaters ? coerceOnOff(heaters.exhaust) : null;
    renderHeaterState(heaterBottomStateEl, bottomOn);
    renderHeaterState(heaterExhaustStateEl, exhaustOn);

    const fluidLog = buildFluidTelemetryModel(fluid, pump);
    const previousEmergencyStop = pumpSafetyState.resetRequired;
    pumpSafetyState = buildPumpSafetyModel(safety, pump);

    latestSnapshot = {
      temps: temps.slice(0, MAX_SENSORS),
      sensorCount,
      valve,
      modeChar,
      pump,
      safety,
      fluid,
      scale,
      control,
    };
    updatePumpTelemetry(pump, pumpSafetyState);
    updateSensorStats();
    updateFluidTelemetry(fluid, pump);
    updateScaleTelemetry(scale);
    updateAutoModeStatus(control);

    if (!previousEmergencyStop && pumpSafetyState.resetRequired) {
      setCommandStatus(pumpSafetyState.message || 'Emergency stop triggered', 'error');
    } else if (previousEmergencyStop && !pumpSafetyState.resetRequired) {
      setCommandStatus('Emergency stop cleared', 'success');
    }

    if (serverLogInfo.active) {
      const currentRows = typeof serverLogInfo.rows === 'number' ? serverLogInfo.rows : 0;
      serverLogInfo.rows = currentRows + 1;
    }

    if (loggingEnabled) {
      const row = [ts];
      for (let i = 0; i < MAX_SENSORS; i += 1) {
        const value = temps[i];
        row.push(Number.isFinite(value) ? value : NaN);
      }
      row.push(valve);
      row.push(modeChar);
      row.push(...extractLogValues(pump, PUMP_LOG_FIELDS));
      row.push(...extractLogValues(fluidLog, FLUID_LOG_FIELDS));
      row.push(...extractLogValues(scale, SCALE_LOG_FIELDS));
      loggingRows.push(row);
    }

    updateLoggingStatusLabel();

    updateChartRanges();
    if (chart) {
      chart.update('none');
    }
    if (pressureChart) {
      pressureChart.update('none');
    }
  }

  async function sendCommand(cmd, options = {}) {
    const { suppressStatus = false } = options;
    try {
      if (!suppressStatus) {
        setCommandStatus(`Sending "${cmd}"…`, 'info');
      }
      await apiJson('/api/command', {
        method: 'POST',
        body: JSON.stringify({ cmd }),
      });
    } catch (err) {
      console.error('Command error', err);
      setCommandStatus(`Command failed: ${err.message}`, 'error');
    }
  }

  async function startLogging() {
    if (loggingEnabled || serverLogInfo.active) {
      setCommandStatus('Logging already active', 'warn');
      updateLoggingButtonState();
      return;
    }
    updateLoggingButtonState({ busy: true });
    try {
      setCommandStatus('Starting logging…', 'info');
      const data = await apiJson('/api/logging/start', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      serverLogInfo = {
        active: Boolean(data.active),
        filename: data.filename || null,
        path: data.path || null,
        rows: typeof data.rows === 'number' ? data.rows : 0,
      };
      loggingEnabled = true;
      loggingRows = [];
      updateLoggingStatusLabel();
      setCommandStatus(`Logging to ${serverLogInfo.path || serverLogInfo.filename || 'server log'}`, 'success');
    } catch (err) {
      console.error('Start logging failed', err);
      setCommandStatus(`Logging start failed: ${err.message}`, 'error');
      loggingEnabled = serverLogInfo.active;
      updateLoggingStatusLabel();
    } finally {
      updateLoggingButtonState();
    }
  }

  async function stopLogging(download = true) {
    if (!loggingEnabled && !serverLogInfo.active) {
      setCommandStatus('Logging not active', 'warn');
      updateLoggingButtonState();
      return;
    }
    updateLoggingButtonState({ busy: true });
    try {
      const data = await apiJson('/api/logging/stop', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      if (data && data.ok) {
        serverLogInfo = {
          active: false,
          filename: data.filename || serverLogInfo.filename,
          path: data.path || serverLogInfo.path,
          rows: typeof data.rows === 'number' ? data.rows : 0,
        };
        const savedPath = serverLogInfo.path || serverLogInfo.filename;
        if (savedPath) {
          setCommandStatus(`Log saved to ${savedPath}`, 'success');
        } else {
          setCommandStatus('Logging stopped', 'success');
        }
      } else {
        serverLogInfo.active = false;
        setCommandStatus('Logging stopped', 'success');
      }
    } catch (err) {
      console.error('Stop logging failed', err);
      setCommandStatus(`Logging stop failed: ${err.message}`, 'error');
    }
    loggingEnabled = false;
    updateLoggingStatusLabel();

    if (download && loggingRows.length > 0) {
      downloadCsv();
    }
    updateLoggingButtonState();
  }

  function downloadCsv() {
    if (!loggingRows.length) {
      setCommandStatus('No rows logged yet', 'warn');
      return;
    }
    const lines = [LOG_HEADER.join(',')];
    for (const row of loggingRows) {
      const formatted = LOG_HEADER.map((column, idx) => formatLogValue(column, row[idx]));
      lines.push(formatted.join(','));
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `log_${stamp}.csv`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => {
      URL.revokeObjectURL(url);
    }, 1000);
    loggingRows = [];
    updateLoggingStatusLabel();
  }

  document.querySelectorAll('button[data-cmd]').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      const cmd = event.currentTarget.getAttribute('data-cmd');
      if (!cmd) {
        return;
      }
      sendCommand(cmd);
    });
  });

  function pumpSliderChanged(event) {
    const next = parsePumpPctInput(event?.target?.value);
    if (!Number.isFinite(next)) {
      return;
    }
    userPumpDirty = true;
    syncPumpInputs(next, { force: true });
  }

  function pumpTextInputChanged(event) {
    userPumpDirty = true;
    const next = parsePumpPctInput(event?.target?.value);
    if (!Number.isFinite(next)) {
      return;
    }
    syncPumpInputs(next, { force: true, preserveTypedInput: true });
  }

  function pumpTextInputBlurred() {
    userPumpDirty = true;
    normalizePumpTextInput();
  }

  if (pumpOverspeedToggle) {
    pumpOverspeedToggle.addEventListener('change', (event) => {
      applyOverspeedToggle(event.target.checked);
    });
  }

  async function handlePumpActionButton() {
    if (pumpSafetyState.resetRequired) {
      await issueEmergencyStopReset();
      return;
    }
    if (pumpRunning) {
      issuePumpStop();
    } else {
      issuePumpStart();
    }
  }

  if (pumpStopButton) {
    pumpStopButton.addEventListener('click', () => {
      handlePumpActionButton().catch(() => {});
    });
  }

  if (globalPumpStopButton) {
    globalPumpStopButton.addEventListener('click', () => {
      handlePumpActionButton().catch(() => {});
    });
  }

  if (pumpCmdSlider) {
    pumpCmdSlider.addEventListener('input', pumpSliderChanged);
  }

  if (pumpCmdInput) {
    pumpCmdInput.addEventListener('input', pumpTextInputChanged);
    pumpCmdInput.addEventListener('blur', pumpTextInputBlurred);
    pumpCmdInput.addEventListener('change', pumpTextInputBlurred);
  }

  if (pumpCmdForm) {
    pumpCmdForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (pumpSafetyState.resetRequired) {
        setCommandStatus('Emergency stop latched. Reset it before setting speed.', 'error');
        return;
      }
      const desiredPct = normalizePumpTextInput();
      const desiredHz = pumpHzFromPct(desiredPct);
      try {
        setCommandStatus('Setting pump speed…', 'info');
        await apiJson('/api/command', {
          method: 'POST',
          body: JSON.stringify({ cmd: `PUMP ${desiredPct.toFixed(2)}` }),
        });
        userPumpDirty = false;
        pumpRunning = desiredPct > 0;
        syncPumpInputs(desiredPct, { force: true });
        updatePumpActionButtons();
        const hzText = Number.isFinite(desiredHz) ? desiredHz.toFixed(2) : '?';
        setCommandStatus(`Pump set to ${desiredPct.toFixed(2)} % (${hzText} Hz)`, 'success');
      } catch (err) {
        console.error('Pump command failed', err);
        setCommandStatus(`Pump command failed: ${err.message}`, 'error');
      }
    });
  }

  if (autoControlForm) {
    autoControlForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!hfeGoalInput || !hxLimitInput || !autoHysteresisInput || !hxApproachInput) {
        return;
      }

      const nextHfeGoalC = parseFloat(hfeGoalInput.value);
      const nextHxLimitC = parseFloat(hxLimitInput.value);
      const nextAutoHysteresisC = parseFloat(autoHysteresisInput.value);
      const nextHxApproachC = parseFloat(hxApproachInput.value);

      if (!Number.isFinite(nextHfeGoalC)) {
        setCommandStatus('Invalid HFE goal value', 'error');
        return;
      }
      if (!Number.isFinite(nextHxLimitC)) {
        setCommandStatus('Invalid HX limit value', 'error');
        return;
      }
      if (!Number.isFinite(nextAutoHysteresisC) || nextAutoHysteresisC < 0) {
        setCommandStatus('Invalid hysteresis value', 'error');
        return;
      }
      if (!Number.isFinite(nextHxApproachC) || nextHxApproachC < 0) {
        setCommandStatus('Invalid HX approach value', 'error');
        return;
      }

      try {
        setCommandStatus('Updating auto-mode targets…', 'info');
        const latestControl =
          latestSnapshot && latestSnapshot.control && typeof latestSnapshot.control === 'object'
            ? latestSnapshot.control
            : null;
        const supportsAtomicAutoTargets =
          latestControl && Object.prototype.hasOwnProperty.call(latestControl, 'auto_close_latched');

        if (supportsAtomicAutoTargets) {
          const autoTargetsCommand = `AUTO TARGETS ${nextHfeGoalC.toFixed(2)} ${nextHxLimitC.toFixed(
            2,
          )} ${nextHxApproachC.toFixed(2)} ${nextAutoHysteresisC.toFixed(2)}`;
          await apiJson('/api/command', {
            method: 'POST',
            body: JSON.stringify({ cmd: autoTargetsCommand }),
          });
        } else {
          const legacyCommands = [
            `HFE GOAL ${nextHfeGoalC.toFixed(2)}`,
            `HX LIMIT ${nextHxLimitC.toFixed(2)}`,
            `HX APPROACH ${nextHxApproachC.toFixed(2)}`,
            `HYSTERESIS ${nextAutoHysteresisC.toFixed(2)}`,
          ];
          for (const cmd of legacyCommands) {
            await apiJson('/api/command', {
              method: 'POST',
              body: JSON.stringify({ cmd }),
            });
          }
        }
        currentHfeGoalC = nextHfeGoalC;
        currentHxLimitC = nextHxLimitC;
        currentAutoHysteresisC = nextAutoHysteresisC;
        currentHxApproachC = nextHxApproachC;
        markLocalAutoTargets();
        setpointSeries.length = 0;
        setpointDataset.data = setpointSeries;
        setpointDataset.label = hfeGoalLabel();
        hfeGoalInput.value = currentHfeGoalC.toFixed(2);
        hxLimitInput.value = currentHxLimitC.toFixed(2);
        autoHysteresisInput.value = currentAutoHysteresisC.toFixed(2);
        hxApproachInput.value = currentHxApproachC.toFixed(2);
        const nextAutoControl =
          latestSnapshot && latestSnapshot.control && typeof latestSnapshot.control === 'object'
            ? { ...latestSnapshot.control }
            : {};
        nextAutoControl.hfe_goal_c = currentHfeGoalC;
        nextAutoControl.setpoint_c = currentHfeGoalC;
        nextAutoControl.hx_limit_c = currentHxLimitC;
        nextAutoControl.thi_limit_c = currentHxLimitC;
        nextAutoControl.ln_hysteresis_c = currentAutoHysteresisC;
        nextAutoControl.hx_approach_c = currentHxApproachC;
        const thiTempForPreview = finiteNumber(nextAutoControl.thi_temp_c);
        const flowTempForPreview = finiteNumber(nextAutoControl.flow_temp_c);
        nextAutoControl.thi_reopen_c = Number.isFinite(flowTempForPreview)
          ? flowTempForPreview - currentHxApproachC
          : null;
        nextAutoControl.flow_reopen_c = currentHfeGoalC + currentAutoHysteresisC;
        nextAutoControl.thi_valid = Number.isFinite(thiTempForPreview);
        nextAutoControl.flow_valid = Number.isFinite(flowTempForPreview);
        let previewCloseRequested = false;
        let previewReason = 'none';
        if (!Number.isFinite(thiTempForPreview)) {
          previewCloseRequested = true;
          previewReason = 'missing_thi';
        } else if (!Number.isFinite(flowTempForPreview)) {
          previewCloseRequested = true;
          previewReason = 'missing_flow_temp';
        } else if (thiTempForPreview <= currentHxLimitC) {
          previewCloseRequested = true;
          previewReason = 'thi_limit';
        } else if (flowTempForPreview <= currentHfeGoalC) {
          previewCloseRequested = true;
          previewReason = 'flow_goal';
        }
        const previewReadyToOpen =
          !previewCloseRequested &&
          Number.isFinite(nextAutoControl.thi_reopen_c) &&
          thiTempForPreview >= nextAutoControl.thi_reopen_c &&
          flowTempForPreview >= nextAutoControl.flow_reopen_c;
        let previewAutoCloseLatched = coerceOnOff(nextAutoControl.auto_close_latched) === true;
        if (previewCloseRequested) {
          previewAutoCloseLatched = true;
        } else if (previewReadyToOpen) {
          previewAutoCloseLatched = false;
        }
        nextAutoControl.close_requested = previewCloseRequested;
        nextAutoControl.ready_to_open = previewReadyToOpen;
        nextAutoControl.auto_close_latched = previewAutoCloseLatched;
        nextAutoControl.within_hysteresis_band =
          previewAutoCloseLatched && !previewCloseRequested && !previewReadyToOpen;
        nextAutoControl.auto_close_reason = previewReason;
        if (latestSnapshot) {
          latestSnapshot.control = nextAutoControl;
        }
        updateAutoModeStatus(nextAutoControl);
        if (chart) {
          chart.update('none');
        }
        setCommandStatus(
          `Auto targets set: HFE goal ${currentHfeGoalC.toFixed(2)} °C, HX limit ${currentHxLimitC.toFixed(
            2,
          )} °C, HX approach ${currentHxApproachC.toFixed(2)} °C, hysteresis ${currentAutoHysteresisC.toFixed(2)} °C`,
          'success',
        );
      } catch (err) {
        console.error('Auto-mode target update failed', err);
        setCommandStatus(`Auto-mode target update failed: ${err.message}`, 'error');
      }
    });
  }

  if (scaleTareForm) {
    scaleTareForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!scaleTareInput) {
        return;
      }
      const nextTareKg = parseFloat(scaleTareInput.value);
      if (!Number.isFinite(nextTareKg) || nextTareKg < 0) {
        setScaleTareStatus('Invalid tare weight', 'error');
        setCommandStatus('Invalid scale tare weight', 'error');
        return;
      }

      try {
        setScaleTareStatus('Saving tare…', 'info');
        const data = await apiJson('/api/scale/tare', {
          method: 'POST',
          body: JSON.stringify({ tare_kg: nextTareKg }),
        });
        const savedTareKg = finiteNumber(data.tare_kg);
        scaleTareKg = Number.isFinite(savedTareKg) ? savedTareKg : nextTareKg;
        syncScaleTareInput({ force: true });
        if (latestSnapshot) {
          latestSnapshot.scale =
            latestSnapshot.scale && typeof latestSnapshot.scale === 'object'
              ? { ...latestSnapshot.scale, tare_kg: scaleTareKg }
              : { tare_kg: scaleTareKg };
        }
        setScaleTareStatus(`Tare set to ${scaleTareKg.toFixed(3)} kg`, 'success');
        setCommandStatus(`Scale tare set to ${scaleTareKg.toFixed(3)} kg`, 'success');
      } catch (err) {
        console.error('Scale tare update failed', err);
        setScaleTareStatus(`Save failed: ${err.message}`, 'error');
        setCommandStatus(`Scale tare update failed: ${err.message}`, 'error');
      }
    });
  }

  if (loggingToggleBtn) {
    loggingToggleBtn.addEventListener('click', () => {
      if (loggingEnabled || serverLogInfo.active) {
        stopLogging(true);
      } else {
        startLogging();
      }
    });
  }
  window.addEventListener('beforeunload', () => {
    if (ws) {
      ws.close();
    }
  });

  if (hfeGoalInput) {
    hfeGoalInput.value = currentHfeGoalC.toFixed(2);
  }
  if (hxLimitInput) {
    hxLimitInput.value = currentHxLimitC.toFixed(2);
  }
  if (autoHysteresisInput) {
    autoHysteresisInput.value = currentAutoHysteresisC.toFixed(2);
  }
  if (hxApproachInput) {
    hxApproachInput.value = currentHxApproachC.toFixed(2);
  }
  updateAutoModeStatus(null);

  syncPumpInputs(lastPumpCmdPct, { force: true });
  updatePumpSafetyStatus();
  updatePumpActionButtons();
  syncScaleTareInput({ force: true });

  updateLoggingStatusLabel();
  updateLoggingButtonState();

  Promise.allSettled([refreshLoggingStatus(), refreshScaleTare()])
    .catch(() => {})
    .finally(() => {
      connectWebSocket();
    });
})();
