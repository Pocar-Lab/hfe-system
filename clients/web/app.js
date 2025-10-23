'use strict';

(function () {
  const MAX_SENSORS = 10;
  const MAX_POINTS = 900;
  const WINDOW_MINUTES = 15;
  const SETPOINT = 25.0;

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
  const sensorCountEl = document.getElementById('sensor-count');
  const validCountEl = document.getElementById('valid-count');
  const avgTempEl = document.getElementById('avg-temp');
  const validListEl = document.getElementById('valid-list');
  const sensorValuesEl = document.getElementById('sensor-values');
  const startLoggingBtn = document.getElementById('start-logging');
  const stopLoggingBtn = document.getElementById('stop-logging');
  const clearLoggingBtn = document.getElementById('clear-logging');
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

  let currentSetpoint = SETPOINT;

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

  const MIN_CHART_CONTENT_HEIGHT = 260;
  const MAX_CHART_CONTENT_HEIGHT = 760;
  const AUTO_COMMAND_COOLDOWN_MS = 1500;
  let pendingChartHeightFrame = null;
  let chartResizeObserver = null;
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

  function setConnectionStatus(text, tone = 'normal') {
    statusEl.textContent = `Status: ${text}`;
    statusEl.dataset.tone = tone;
  }

  function setLoggingStatus(text) {
    loggingStatusEl.textContent = `Logging: ${text}`;
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
    if (latestSnapshot) {
      latestSnapshot.avgSelected = Number.isFinite(avgValue) ? avgValue : null;
      latestSnapshot.selectedValid = selectedValid;
    }
    scheduleChartHeightUpdate();
  }

  function setCommandStatus(text, tone = 'normal') {
    if (!['alert', 'warn', 'error'].includes(tone)) {
      return;
    }
    commandStatusEl.textContent = text;
    commandStatusEl.dataset.tone = tone;
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
      startLoggingBtn.disabled = serverLogInfo.active;
      stopLoggingBtn.disabled = !serverLogInfo.active;
      clearLoggingBtn.disabled = loggingRows.length === 0;
    } catch (err) {
      console.warn('Logging status fetch failed', err);
      updateLoggingStatusLabel();
      startLoggingBtn.disabled = false;
      stopLoggingBtn.disabled = true;
      clearLoggingBtn.disabled = loggingRows.length === 0;
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

  function updateChartRanges() {
    chart.options.scales.x.min = 0;
    chart.options.scales.x.max = WINDOW_MINUTES;
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
    const tMin = (ts - startEpochSec) / 60;

    for (let i = 0; i < MAX_SENSORS; i += 1) {
      const value = Number.isFinite(temps[i]) ? temps[i] : null;
      pushSeries(sensorSeries[i], value === null ? { x: tMin, y: null } : { x: tMin, y: value });
      sensorDatasets[i].data = sensorSeries[i];
      sensorDatasets[i].hidden = i >= sensorCount;
    }

    pushSeries(setpointSeries, { x: tMin, y: currentSetpoint });
    setpointDataset.data = setpointSeries;

    const valve = Number.isFinite(data.valve) ? Number(data.valve) : 0;

    const valveOpen = Boolean(valve);
    valveStateEl.textContent = valveOpen ? 'OPEN' : 'CLOSED';
    valveStateEl.classList.toggle('valve-open', valveOpen);
    valveStateEl.classList.toggle('valve-closed', !valveOpen);

    const modeChar = typeof data.mode === 'string' ? data.mode.charAt(0).toUpperCase() : 'A';
    const hardwareAuto = modeChar === 'A';
    if (hardwareAuto) {
      clientAutoActive = true;
    }
    const modeText =
      clientAutoActive
        ? 'AUTO'
        : modeChar === 'O'
        ? 'FORCED OPEN'
        : modeChar === 'C'
        ? 'FORCED CLOSE'
        : 'AUTO';
    modeStateEl.textContent = `Mode: ${modeText}`;

    latestSnapshot = {
      temps: temps.slice(0, MAX_SENSORS),
      sensorCount,
      valve,
      modeChar,
    };
    updateSensorStats();
    maybeRunAutoValve(valveOpen, modeChar);

    if (serverLogInfo.active) {
      const currentRows = typeof serverLogInfo.rows === 'number' ? serverLogInfo.rows : 0;
      serverLogInfo.rows = currentRows + 1;
    }

    if (loggingEnabled) {
      const row = [ts];
      for (let i = 0; i < MAX_SENSORS; i += 1) {
        row.push(Number.isFinite(temps[i]) ? temps[i] : 'nan');
      }
      row.push(valve);
      row.push(modeChar);
      loggingRows.push(row);
      clearLoggingBtn.disabled = false;
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
  function maybeRunAutoValve(valveOpen, modeChar) {
    const autoEnabled = clientAutoActive || modeChar === 'A';
    if (!autoEnabled) {
      autoValveDesiredState = null;
      return;
    }
    clientAutoActive = true;
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
    const shouldOpen = avg > currentSetpoint;
    if (shouldOpen === valveOpen) {
      autoValveDesiredState = shouldOpen;
      return;
    }
    const now = Date.now();
    if (autoValveDesiredState === shouldOpen && now - autoValveLastCommandTs < AUTO_COMMAND_COOLDOWN_MS) {
      return;
    }
    autoValveDesiredState = shouldOpen;
    autoValveLastCommandTs = now;
    sendCommand(shouldOpen ? 'VALVE OPEN' : 'VALVE CLOSE', { suppressStatus: true }).catch(() => {});
  }

  async function startLogging() {
    if (serverLogInfo.active) {
      setCommandStatus('Logging already active', 'warn');
      return;
    }
    try {
      setCommandStatus('Starting logging…', 'info');
      startLoggingBtn.disabled = true;
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
      startLoggingBtn.disabled = true;
      stopLoggingBtn.disabled = false;
      clearLoggingBtn.disabled = true;
      setCommandStatus(`Logging to ${serverLogInfo.path || serverLogInfo.filename || 'server log'}`, 'success');
    } catch (err) {
      console.error('Start logging failed', err);
      setCommandStatus(`Logging start failed: ${err.message}`, 'error');
      loggingEnabled = serverLogInfo.active;
      updateLoggingStatusLabel();
      startLoggingBtn.disabled = serverLogInfo.active;
      stopLoggingBtn.disabled = !serverLogInfo.active;
    }
  }

  async function stopLogging(download = true) {
    if (!loggingEnabled && !serverLogInfo.active) {
      setCommandStatus('Logging not active', 'warn');
      return;
    }
    try {
      stopLoggingBtn.disabled = true;
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
    startLoggingBtn.disabled = serverLogInfo.active;
    stopLoggingBtn.disabled = !serverLogInfo.active;
    updateLoggingStatusLabel();
    clearLoggingBtn.disabled = loggingRows.length === 0;

    if (download && loggingRows.length > 0) {
      downloadCsv();
    }
  }

  function clearLogging() {
    loggingRows = [];
    updateLoggingStatusLabel();
    clearLoggingBtn.disabled = true;
  }

  function downloadCsv() {
    if (!loggingRows.length) {
      setCommandStatus('No rows logged yet', 'warn');
      return;
    }
    const header = ['time_s'];
    for (let i = 0; i < MAX_SENSORS; i += 1) {
      header.push(`temp${i}_C`);
    }
    header.push('valve', 'mode');

    const lines = [header.join(',')];
    for (const row of loggingRows) {
      const formatted = row.map((value, idx) => {
        if (idx === 0) {
          return typeof value === 'number' ? value.toFixed(3) : 'nan';
        }
        if (idx <= MAX_SENSORS) {
          if (typeof value === 'number') {
            return Number.isFinite(value) ? value.toFixed(2) : 'nan';
          }
          return value;
        }
        if (idx === MAX_SENSORS + 1) {
          return Number(value);
        }
        return String(value || '');
      });
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
    clearLoggingBtn.disabled = true;
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

  startLoggingBtn.addEventListener('click', () => startLogging());
  stopLoggingBtn.addEventListener('click', () => stopLogging(true));
  clearLoggingBtn.addEventListener('click', () => {
    clearLogging();
  });

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
    hysteresisInput.value = Number.isFinite(initialH) ? initialH.toFixed(2) : '0.50';
  }
  if (telemetryInput && (!telemetryInput.value || Number(telemetryInput.value) <= 0)) {
    telemetryInput.value = '1000';
  }

  renderSensorCheckboxes(0);

  updateLoggingStatusLabel();

  refreshLoggingStatus()
    .catch(() => {})
    .finally(() => {
      connectWebSocket();
    });
})();
