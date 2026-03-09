'use strict';

(function () {
  const MAX_SENSORS = 10;
  const MAX_POINTS = 900;
  const WINDOW_MINUTES = 15;
  const SETPOINT = 25.0;
  const PUMP_MAX_CMD_PCT = 100.0;
  const PUMP_MAX_FREQ_HZ = 71.7;
  const PUMP_SAFE_MAX_HZ = 60.0;
  const FLUID_REFERENCE = {
    name: 'HFE-7200',
    concentrationPct: 100.0,
  };
  // The meter currently reports register 30006 in Fahrenheit; the UI renders it in Celsius.
  const FLOW_TEMPERATURE_SOURCE_UNIT = 'fahrenheit';
  const PUMP_LOG_FIELDS = [
    { column: 'pump_cmd_pct', key: 'cmd_pct', digits: 3 },
    { column: 'pump_cmd_hz', key: 'cmd_hz', digits: 3 },
    { column: 'pump_freq_hz', key: 'freq_hz', digits: 3 },
    { column: 'pump_freq_pct', key: 'freq_pct', digits: 2 },
    { column: 'pump_input_power_w', key: 'input_power_w', digits: 2 },
    { column: 'pump_input_power_pct', key: 'input_power_pct', digits: 2 },
    { column: 'pump_output_current_a', key: 'output_current_a', digits: 3 },
    { column: 'pump_output_current_pct', key: 'output_current_pct', digits: 2 },
    { column: 'pump_output_voltage_v', key: 'output_voltage_v', digits: 2 },
    { column: 'pump_output_voltage_pct', key: 'output_voltage_pct', digits: 2 },
    { column: 'pump_pressure_before_bar', key: 'pressure_before_bar', digits: 3 },
    { column: 'pump_pressure_after_bar', key: 'pressure_after_bar', digits: 3 },
    { column: 'pump_pressure_tank_bar', key: 'pressure_tank_bar', digits: 3 },
    { column: 'pump_pressure_after_v', key: 'pressure_after_v', digits: 3 },
    { column: 'pump_pressure_before_psi', key: 'pressure_before_psi', digits: 2 },
    { column: 'pump_pressure_after_psi', key: 'pressure_after_psi', digits: 2 },
    { column: 'pump_pressure_tank_psi', key: 'pressure_tank_psi', digits: 2 },
    { column: 'pump_pressure_error_bar', key: 'pressure_error_bar', digits: 3 },
    { column: 'pump_max_freq_hz', key: 'max_freq_hz', digits: 3 },
  ];
  const FLUID_LOG_FIELDS = [
    { column: 'fluid_meter_valid', key: 'meter_valid', digits: 0 },
    { column: 'fluid_concentration_pct', key: 'concentration_pct', digits: 1 },
    { column: 'fluid_flow_velocity_mps', key: 'flow_velocity_mps', digits: 6 },
    { column: 'fluid_volume_flow_m3s', key: 'volume_flow_m3s', digits: 9 },
    { column: 'fluid_mass_flow_kgs', key: 'mass_flow_kgs', digits: 9 },
    { column: 'fluid_temperature_raw', key: 'temperature_raw', digits: 6 },
    { column: 'fluid_temperature_c', key: 'temperature_c', digits: 3 },
    { column: 'fluid_density_kg_m3', key: 'density_kg_m3', digits: 6 },
    { column: 'fluid_delta_p_bar', key: 'delta_p_bar', digits: 3 },
  ];
  const LOG_HEADER = [
    'time_s',
    ...Array.from({ length: MAX_SENSORS }, (_, idx) => `temp${idx}_C`),
    'valve',
    'mode',
    ...PUMP_LOG_FIELDS.map((field) => field.column),
    ...FLUID_LOG_FIELDS.map((field) => field.column),
  ];
  const LOG_FIELD_DIGITS = new Map(
    [...PUMP_LOG_FIELDS, ...FLUID_LOG_FIELDS].map((field) => [field.column, field.digits || 3]),
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
  const pumpStopButton = document.getElementById('pump-stop-button');
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
  const sensorCountEl = document.getElementById('sensor-count');
  const validCountEl = document.getElementById('valid-count');
  const avgTempEl = document.getElementById('avg-temp');
  const validListEl = document.getElementById('valid-list');
  const overviewConnectionEl = document.getElementById('overview-connection');
  const overviewValveEl = document.getElementById('overview-valve');
  const overviewAvgTempEl = document.getElementById('overview-avg-temp');
  const overviewTankPressureEl = document.getElementById('overview-tank-pressure');
  const overviewTankPressureSubEl = document.getElementById('overview-tank-pressure-sub');
  const sensorValuesEl = document.getElementById('sensor-values');
  const loggingToggleBtn = document.getElementById('logging-toggle');
  const fluidNameEl = document.getElementById('fluid-name');
  const fluidConcentrationEl = document.getElementById('fluid-concentration');
  const fluidPressureDropEl = document.getElementById('fluid-pressure-drop');
  const fluidPressureDropSubEl = document.getElementById('fluid-pressure-drop-sub');
  const fluidFlowVelocityEl = document.getElementById('fluid-flow-velocity');
  const fluidVolumeFlowEl = document.getElementById('fluid-volume-flow');
  const fluidMassFlowEl = document.getElementById('fluid-mass-flow');
  const fluidTemperatureEl = document.getElementById('fluid-temperature');
  const fluidDensityEl = document.getElementById('fluid-density');
  const fluidTempRiseEl = document.getElementById('fluid-temp-rise');
  const fluidViscosityEl = document.getElementById('fluid-viscosity');
  const fluidMixingEfficiencyEl = document.getElementById('fluid-mixing-efficiency');
  const setpointForm = document.getElementById('setpoint-form');
  const setpointInput = document.getElementById('setpoint-input');
  const hysteresisInput = document.getElementById('hysteresis-input');
  const telemetryInput = document.getElementById('telemetry-input');
  const sensorCheckboxesEl = document.getElementById('sensor-checkboxes');
  const chartSectionEl = document.getElementById('chart-section');
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

  const ctx = document.getElementById('temp-chart').getContext('2d');
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

  const sensorDatasets = Array.from({ length: MAX_SENSORS }, (_, idx) => ({
    label: `U${idx}`,
    borderColor: SENSOR_COLORS[idx % SENSOR_COLORS.length],
    backgroundColor: 'rgba(0,0,0,0)',
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.1,
    spanGaps: true,
    data: [],
  }));

  function sentenceCase(text) {
    const value = text === undefined || text === null ? '' : String(text);
    const trimmed = value.trim();
    if (!trimmed) {
      return '';
    }
    return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
  }

  let currentSetpoint = SETPOINT;
  let currentHysteresis = 0.5;
  let pumpMaxFreqHz = PUMP_MAX_FREQ_HZ;
  let lastPumpCmdPct = 0;
  let userPumpDirty = false;
  let overspeedEnabled = false;

  function setpointLabel() {
    return `Set-point (${currentSetpoint.toFixed(1)} °C)`;
  }

  const setpointDataset = {
    label: setpointLabel(),
    borderColor: '#adb5bd',
    borderWidth: 1,
    borderDash: [6, 6],
    pointRadius: 0,
    tension: 0,
    spanGaps: true,
    data: [],
  };

  const chart = new Chart(ctx, {
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
        x: {
          type: 'linear',
          title: { display: true, text: 'Time (min)' },
          min: 0,
          max: WINDOW_MINUTES,
          ticks: { color: tickColor },
          grid: { color: gridColor },
        },
        y: {
          title: { display: true, text: 'Temperature (°C)' },
          suggestedMin: -170,
          suggestedMax: 25,
          ticks: { color: tickColor },
          grid: { color: gridColor },
        },
      },
      plugins: {
        legend: {
          labels: {
            color: legendLabelColor,
          },
        },
      },
    },
  });

  const MIN_CHART_CONTENT_HEIGHT = 300;
  const MAX_CHART_CONTENT_HEIGHT = 900;
  const AUTO_COMMAND_COOLDOWN_MS = 1500;
  let pendingChartHeightFrame = null;
  let chartResizeObserver = null;
  setActivePage(activePage);
  let autoValveDesiredState = null;
  let autoValveLastCommandTs = 0;
  let clientAutoActive = false;

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
    const contentHeight = Math.round(target);
    const currentHeight = parseFloat(chartSectionEl.style.height || 0);
    if (Number.isFinite(currentHeight) && Math.abs(currentHeight - contentHeight) < 1) {
      return;
    }
    chartSectionEl.style.height = `${contentHeight}px`;
    chartSectionEl.style.minHeight = `${contentHeight}px`;
    if (chart && chart.canvas) {
      chart.canvas.style.height = '100%';
      chart.canvas.style.minHeight = `${Math.max(180, contentHeight - 32)}px`;
    }
    chart.resize();
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

  let sensorSelections = Array(MAX_SENSORS).fill(true);
  let renderedCheckboxCount = 0;
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
    if (column.startsWith('pump_') || column.startsWith('fluid_')) {
      const digits = LOG_FIELD_DIGITS.get(column) || 3;
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

  function ensureSensorSelections(count) {
    for (let i = 0; i < count; i += 1) {
      if (typeof sensorSelections[i] !== 'boolean') {
        sensorSelections[i] = true;
      }
    }
  }

  function renderSensorCheckboxes(count) {
    if (!sensorCheckboxesEl) {
      return;
    }
    sensorCheckboxesEl.innerHTML = '';
    if (!count) {
      sensorCheckboxesEl.innerHTML = '<p class="muted">No sensors detected yet.</p>';
      renderedCheckboxCount = 0;
      return;
    }
    ensureSensorSelections(count);
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < count; i += 1) {
      const label = document.createElement('label');
      label.className = 'sensor-checkbox';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = sensorSelections[i] !== false;
      input.dataset.index = String(i);
      input.addEventListener('change', onSensorCheckboxChange);
      const span = document.createElement('span');
      span.textContent = `U${i}`;
      label.appendChild(input);
      label.appendChild(span);
      fragment.appendChild(label);
    }
    sensorCheckboxesEl.appendChild(fragment);
    renderedCheckboxCount = count;
  }

  function onSensorCheckboxChange(event) {
    const idx = Number(event.currentTarget.dataset.index);
    if (Number.isNaN(idx)) {
      return;
    }
    sensorSelections[idx] = event.currentTarget.checked;
    updateSensorStats();
  }

  function updateSensorStats() {
    if (!latestSnapshot) {
      return;
    }
    const { temps, sensorCount } = latestSnapshot;
    if (!sensorValuesEl) {
      return;
    }
    if (sensorCount !== renderedCheckboxCount) {
      renderSensorCheckboxes(sensorCount);
    } else {
      ensureSensorSelections(sensorCount);
    }

    let validNow = 0;
    let selectedValid = 0;
    let selectedSum = 0;
    const selectedLabels = [];
    const chips = [];

    for (let i = 0; i < sensorCount; i += 1) {
      const value = temps[i];
      const finite = Number.isFinite(value);
      const selected = sensorSelections[i] !== false;
      const classes = ['sensor-chip'];
      if (selected) {
        classes.push('selected');
      } else {
        classes.push('excluded');
      }
      if (!finite) {
        classes.push('inactive');
      }
      const displayValue = finite ? `${value.toFixed(2)} °C` : '—';
      chips.push(`<div class="${classes.join(' ')}">U${i}: ${displayValue}</div>`);
      if (finite) {
        validNow += 1;
        if (selected) {
          selectedValid += 1;
          selectedSum += value;
          selectedLabels.push(`U${i}`);
        }
      }
    }

    sensorValuesEl.innerHTML = chips.length ? chips.join('') : '<p class="muted">No telemetry yet.</p>';
    sensorCountEl.textContent = `Active: ${sensorCount}`;
    validCountEl.textContent = `Valid now: ${validNow} • Selected: ${selectedValid}`;
    validListEl.textContent = selectedLabels.length ? `Included sensors: ${selectedLabels.join(', ')}` : 'Included sensors: —';
    const avgValue = selectedValid ? selectedSum / selectedValid : NaN;
    avgTempEl.textContent = Number.isFinite(avgValue) ? `${avgValue.toFixed(2)} °C` : '—';
    if (overviewAvgTempEl) {
      overviewAvgTempEl.textContent = Number.isFinite(avgValue)
        ? `${avgValue.toFixed(2)} °C`
        : '—';
    }
    if (latestSnapshot) {
      latestSnapshot.avgSelected = Number.isFinite(avgValue) ? avgValue : null;
      latestSnapshot.selectedValid = selectedValid;
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
      subEl.textContent = Number.isFinite(pctValue) ? `${pctValue.toFixed(1)} %` : '—';
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
    const temperature = convertFlowTemperature(temperatureRaw);
    const beforeBar = pump ? finiteNumber(pump.pressure_before_bar_abs) : NaN;
    const afterBar = pump ? finiteNumber(pump.pressure_after_bar_abs) : NaN;
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

  function syncPumpInputs(pct, { force = false } = {}) {
    const clamped = clampPumpPct(pct);
    lastPumpCmdPct = clamped;
    const asText = clamped.toFixed(2);
    if (!userPumpDirty || force) {
      if (pumpCmdInput) {
        pumpCmdInput.value = asText;
        pumpCmdInput.max = currentMaxPumpPct().toFixed(1);
      }
      if (pumpCmdSlider) {
        pumpCmdSlider.value = asText;
        pumpCmdSlider.max = currentMaxPumpPct().toFixed(1);
      }
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
  }

  function updateChartRanges() {
    chart.options.scales.x.min = 0;
    chart.options.scales.x.max = WINDOW_MINUTES;
  }

  function updatePumpTelemetry(pumpData) {
    const pump = pumpData && typeof pumpData === 'object' ? pumpData : null;
    if (pump && Number.isFinite(pump.max_freq_hz) && pump.max_freq_hz > 0) {
      pumpMaxFreqHz = pump.max_freq_hz;
    }

    const cmdPct = pump && Number.isFinite(pump.cmd_pct) ? clampPumpPct(pump.cmd_pct) : lastPumpCmdPct;
    const cmdHz =
      pump && Number.isFinite(pump.cmd_hz) ? pump.cmd_hz : pumpHzFromPct(cmdPct, pumpMaxFreqHz);
    if (!userPumpDirty) {
      syncPumpInputs(cmdPct, { force: false });
    }

    const freqHz = pump && Number.isFinite(pump.freq_hz) ? pump.freq_hz : null;
    const freqPct =
      pump && Number.isFinite(pump.freq_pct)
        ? pump.freq_pct
        : Number.isFinite(freqHz) && Number.isFinite(pumpMaxFreqHz) && pumpMaxFreqHz > 0
        ? (freqHz / pumpMaxFreqHz) * 100
        : null;

    const currentA = pump && Number.isFinite(pump.output_current_a) ? pump.output_current_a : null;
    const currentPct =
      pump && Number.isFinite(pump.output_current_pct) ? pump.output_current_pct : null;
    const voltageV = pump && Number.isFinite(pump.output_voltage_v) ? pump.output_voltage_v : null;
    const voltagePct =
      pump && Number.isFinite(pump.output_voltage_pct) ? pump.output_voltage_pct : null;
    const powerW = pump && Number.isFinite(pump.input_power_w) ? pump.input_power_w : null;
    const powerPct = pump && Number.isFinite(pump.input_power_pct) ? pump.input_power_pct : null;

    const overviewHz = Number.isFinite(freqHz) ? freqHz : cmdHz;

    if (overviewPumpSpeedEl) {
      overviewPumpSpeedEl.textContent = Number.isFinite(overviewHz)
        ? `${overviewHz.toFixed(2)} Hz`
        : '—';
    }
    if (overviewPumpSpeedSubEl) {
      overviewPumpSpeedSubEl.textContent = '';
    }

    const running = Number.isFinite(freqHz) ? freqHz > 0.2 : cmdPct > 0.2;
    if (pumpRunStateEl) {
      pumpRunStateEl.textContent = running ? 'Running' : 'Stopped';
      pumpRunStateEl.classList.toggle('valve-open', running);
      pumpRunStateEl.classList.toggle('valve-closed', !running);
    }
    if (pumpCmdHzEl) {
      pumpCmdHzEl.textContent = Number.isFinite(cmdHz) ? `${cmdHz.toFixed(2)} Hz` : '—';
    }
    if (pumpCmdRpmEl) {
      const estRpm = Number.isFinite(cmdHz) && pumpMaxFreqHz > 0 ? (cmdHz / pumpMaxFreqHz) * 2150 : null;
      pumpCmdRpmEl.textContent = Number.isFinite(estRpm) ? `${estRpm.toFixed(0)} rpm` : '—';
    }
    if (pumpCmdFlowEl) {
      const estFlow = Number.isFinite(cmdHz) && pumpMaxFreqHz > 0 ? (cmdHz / pumpMaxFreqHz) * 4.0 : null;
      pumpCmdFlowEl.textContent = Number.isFinite(estFlow) ? `${estFlow.toFixed(2)} L/min (est.)` : '—';
    }

    renderMetric(vfdFrequencyEl, vfdFrequencyPctEl, freqHz, 2, freqPct);
    renderMetric(vfdCurrentEl, vfdCurrentPctEl, currentA, 2, currentPct);
    renderMetric(vfdVoltageEl, vfdVoltagePctEl, voltageV, 1, voltagePct);
    renderMetric(vfdPowerEl, vfdPowerPctEl, powerW, 1, powerPct);
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
    const tankBar =
      pump && Number.isFinite(pump.pressure_tank_bar_abs) ? pump.pressure_tank_bar_abs : null;
    const afterVolts =
      pump && Number.isFinite(pump.pressure_after_v) ? pump.pressure_after_v : null;

    if (pumpPressureBeforeEl) {
      pumpPressureBeforeEl.textContent = Number.isFinite(beforeBar) ? beforeBar.toFixed(2) : '—';
    }
    if (pumpPressureAfterEl) {
      const value = Number.isFinite(afterBar) ? afterBar : null;
      pumpPressureAfterEl.textContent = Number.isFinite(value) ? value.toFixed(2) : '—';
    }
    if (overviewTankPressureEl) {
      overviewTankPressureEl.textContent = Number.isFinite(tankBar) ? tankBar.toFixed(2) : '—';
    }
    if (overviewTankPressureSubEl) {
      overviewTankPressureSubEl.textContent = pressureUnitText;
    }
  }

  function updateFluidTelemetry(fluidData, pumpData) {
    const fluidModel = buildFluidTelemetryModel(fluidData, pumpData);
    const temperature = convertFlowTemperature(fluidModel.temperature_raw);
    const volumeFlowLMin = Number.isFinite(fluidModel.volume_flow_m3s)
      ? fluidModel.volume_flow_m3s * 60000
      : NaN;
    const massFlowKgMin = Number.isFinite(fluidModel.mass_flow_kgs)
      ? fluidModel.mass_flow_kgs * 60
      : NaN;

    if (fluidNameEl) {
      fluidNameEl.textContent = fluidModel.name;
    }
    if (fluidConcentrationEl) {
      const digits = Number.isInteger(fluidModel.concentration_pct) ? 0 : 1;
      fluidConcentrationEl.textContent = `${fluidModel.concentration_pct.toFixed(digits)}% composition`;
    }
    if (fluidPressureDropEl) {
      fluidPressureDropEl.textContent = formatNumber(fluidModel.delta_p_bar, 3, ' bar');
    }
    if (fluidPressureDropSubEl) {
      fluidPressureDropSubEl.textContent = 'After pump - before pump';
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
      fluidTemperatureEl.textContent = temperature.main;
    }

    if (fluidDensityEl) {
      fluidDensityEl.textContent = formatNumber(fluidModel.density_kg_m3, 4, ' kg/m³');
    }

    if (fluidTempRiseEl) {
      const avgSelected =
        latestSnapshot && Number.isFinite(latestSnapshot.avgSelected)
          ? latestSnapshot.avgSelected
          : NaN;
      if (Number.isFinite(avgSelected) && Number.isFinite(temperature.celsius)) {
        const proxyDelta = avgSelected - temperature.celsius;
        fluidTempRiseEl.textContent = `${proxyDelta.toFixed(2)} °C`;
      } else {
        fluidTempRiseEl.textContent = 'Awaiting inlet/outlet mapping';
      }
    }

    if (fluidViscosityEl) {
      fluidViscosityEl.textContent = 'Awaiting calibrated hydraulic model';
    }

    if (fluidMixingEfficiencyEl) {
      fluidMixingEfficiencyEl.textContent = 'Reserved';
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

    for (let i = 0; i < MAX_SENSORS; i += 1) {
      const value = Number.isFinite(temps[i]) ? temps[i] : null;
      pushSeries(sensorSeries[i], value === null ? { x: tMin, y: null } : { x: tMin, y: value });
      sensorDatasets[i].data = sensorSeries[i];
      sensorDatasets[i].hidden = i >= sensorCount;
    }

    pushSeries(setpointSeries, { x: tMin, y: currentSetpoint });
    setpointDataset.data = setpointSeries;

    if (tMin > WINDOW_MINUTES) {
      const overflow = tMin - WINDOW_MINUTES;
      shiftAllSeriesLeft(overflow);
      startEpochSec += overflow * 60;
      tMin -= overflow;
    }

    const pump = data && typeof data.pump === 'object' ? data.pump : null;
    const fluid = data && typeof data.fluid === 'object' ? data.fluid : null;

    const valve = Number.isFinite(data.valve) ? Number(data.valve) : 0;

    const valveOpen = Boolean(valve);
    const valveLabel = valveOpen ? 'Open' : 'Closed';
    valveStateEl.textContent = valveLabel;
    valveStateEl.classList.toggle('valve-open', valveOpen);
    valveStateEl.classList.toggle('valve-closed', !valveOpen);

    const modeCharRaw = typeof data.mode === 'string' ? data.mode : '';
    const modeChar = modeCharRaw ? modeCharRaw.charAt(0).toUpperCase() : '';
    let modeText;
    if (clientAutoActive) {
      modeText = 'Auto';
    } else if (modeChar === 'A') {
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
      if (modeText && modeText !== '—') {
        overviewValveEl.textContent = `${valveLabel} · ${modeText}`;
      } else {
        overviewValveEl.textContent = valveLabel;
      }
    }

    const heaters = data && typeof data.heaters === 'object' ? data.heaters : null;
    const bottomOn = heaters ? coerceOnOff(heaters.bottom) : null;
    const exhaustOn = heaters ? coerceOnOff(heaters.exhaust) : null;
    renderHeaterState(heaterBottomStateEl, bottomOn);
    renderHeaterState(heaterExhaustStateEl, exhaustOn);

    const fluidLog = buildFluidTelemetryModel(fluid, pump);

    latestSnapshot = {
      temps: temps.slice(0, MAX_SENSORS),
      sensorCount,
      valve,
      modeChar,
      pump,
      fluid,
    };
    updatePumpTelemetry(pump);
    updateSensorStats();
    updateFluidTelemetry(fluid, pump);
    maybeRunAutoValve(valveOpen);

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
      loggingRows.push(row);
    }

    updateLoggingStatusLabel();

    updateChartRanges();
    chart.update('none');
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

  // Auto mode: drive the physical valve based on the selected sensor average.
  function maybeRunAutoValve(valveOpen) {
    if (!clientAutoActive) {
      autoValveDesiredState = null;
      return;
    }
    if (!latestSnapshot || typeof latestSnapshot.avgSelected !== 'number') {
      return;
    }
    const selectedValid =
      typeof latestSnapshot.selectedValid === 'number' ? latestSnapshot.selectedValid : 0;
    if (selectedValid <= 0) {
      autoValveDesiredState = null;
      return;
    }
    const avg = latestSnapshot.avgSelected;
    if (!Number.isFinite(avg)) {
      return;
    }
    const hysteresis =
      Number.isFinite(currentHysteresis) && currentHysteresis > 0 ? currentHysteresis : 0;
    const upperThreshold = currentSetpoint + hysteresis;
    const lowerThreshold = currentSetpoint - hysteresis;

    let desiredState = autoValveDesiredState;
    if (desiredState === null) {
      desiredState = valveOpen;
    }

    if (!valveOpen && avg > upperThreshold) {
      desiredState = true;
    } else if (valveOpen && avg < lowerThreshold) {
      desiredState = false;
    } else {
      autoValveDesiredState = desiredState;
      return;
    }
    if (desiredState === valveOpen) {
      autoValveDesiredState = desiredState;
      return;
    }
    const now = Date.now();
    if (
      autoValveDesiredState === desiredState &&
      now - autoValveLastCommandTs < AUTO_COMMAND_COOLDOWN_MS
    ) {
      return;
    }
    autoValveDesiredState = desiredState;
    autoValveLastCommandTs = now;
    sendCommand(desiredState ? 'VALVE OPEN' : 'VALVE CLOSE', { suppressStatus: true }).catch(
      () => {},
    );
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
    const filename = `tc_log_${stamp}.csv`;
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
      if (cmd === 'VALVE AUTO') {
        clientAutoActive = true;
        autoValveDesiredState = null;
      } else if (cmd === 'VALVE OPEN' || cmd === 'VALVE CLOSE') {
        clientAutoActive = false;
        autoValveDesiredState = null;
      }
      sendCommand(cmd);
    });
  });

  function pumpInputChanged(event) {
    const next = parseFloat(event?.target?.value || '0');
    if (!Number.isFinite(next)) {
      return;
    }
    userPumpDirty = true;
    syncPumpInputs(next, { force: true });
  }

  if (pumpOverspeedToggle) {
    pumpOverspeedToggle.addEventListener('change', (event) => {
      applyOverspeedToggle(event.target.checked);
    });
  }

  if (pumpStopButton) {
    pumpStopButton.addEventListener('click', () => {
      userPumpDirty = false;
      syncPumpInputs(0, { force: true });
      sendCommand('PUMP 0');
      setCommandStatus('Pump stop issued (0%)', 'info');
    });
  }

  [pumpCmdSlider, pumpCmdInput].forEach((el) => {
    if (el) {
      el.addEventListener('input', pumpInputChanged);
    }
  });

  if (pumpCmdForm) {
    pumpCmdForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const desiredPct = clampPumpPct(
        parseFloat(
          (pumpCmdInput && pumpCmdInput.value) ||
            (pumpCmdSlider && pumpCmdSlider.value) ||
            `${lastPumpCmdPct}`,
        ),
      );
      const desiredHz = pumpHzFromPct(desiredPct);
      try {
        setCommandStatus('Setting pump speed…', 'info');
        await apiJson('/api/command', {
          method: 'POST',
          body: JSON.stringify({ cmd: `PUMP ${desiredPct.toFixed(2)}` }),
        });
        userPumpDirty = false;
        syncPumpInputs(desiredPct, { force: true });
        const hzText = Number.isFinite(desiredHz) ? desiredHz.toFixed(2) : '?';
        setCommandStatus(`Pump set to ${desiredPct.toFixed(2)} % (${hzText} Hz)`, 'success');
      } catch (err) {
        console.error('Pump command failed', err);
        setCommandStatus(`Pump command failed: ${err.message}`, 'error');
      }
    });
  }

  if (setpointForm) {
    setpointForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!setpointInput) {
        return;
      }

      const setpoint = parseFloat(setpointInput.value);
      if (!Number.isFinite(setpoint)) {
        setCommandStatus('Invalid setpoint value', 'error');
        return;
      }

      const hysteresis = hysteresisInput ? parseFloat(hysteresisInput.value) : NaN;
      const telemetryMs = telemetryInput ? parseInt(telemetryInput.value, 10) : NaN;

      const payload = {
        id: 'set_control',
        setpoint_C: setpoint,
        hysteresis_C: Number.isFinite(hysteresis) ? hysteresis : 0.5,
        telemetry_ms: Number.isFinite(telemetryMs) ? Math.max(100, telemetryMs) : 1000,
      };

      try {
        setCommandStatus('Updating setpoint…', 'info');
        await apiJson('/api/command', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        currentSetpoint = payload.setpoint_C;
        currentHysteresis = payload.hysteresis_C;
        setpointSeries.length = 0;
        setpointDataset.data = setpointSeries;
        setpointDataset.label = setpointLabel();
        if (setpointInput) {
          setpointInput.value = payload.setpoint_C.toFixed(2);
        }
        if (hysteresisInput) {
          hysteresisInput.value = payload.hysteresis_C.toFixed(2);
        }
        if (telemetryInput) {
          telemetryInput.value = String(payload.telemetry_ms);
        }
        if (latestSnapshot) {
          updateSensorStats();
        }
        chart.update('none');
        setCommandStatus(`Setpoint set to ${payload.setpoint_C.toFixed(2)} °C`, 'success');
      } catch (err) {
        console.error('Setpoint update failed', err);
        setCommandStatus(`Setpoint update failed: ${err.message}`, 'error');
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

  if (setpointInput) {
    setpointInput.value = currentSetpoint.toFixed(2);
  }
  if (hysteresisInput) {
    const initialH = parseFloat(hysteresisInput.value);
    const safeH = Number.isFinite(initialH) ? initialH : 0.5;
    hysteresisInput.value = safeH.toFixed(2);
    currentHysteresis = safeH;
  }
  if (telemetryInput && (!telemetryInput.value || Number(telemetryInput.value) <= 0)) {
    telemetryInput.value = '1000';
  }

  syncPumpInputs(lastPumpCmdPct, { force: true });
  renderSensorCheckboxes(0);

  updateLoggingStatusLabel();
  updateLoggingButtonState();

  refreshLoggingStatus()
    .catch(() => {})
    .finally(() => {
      connectWebSocket();
    });
})();
