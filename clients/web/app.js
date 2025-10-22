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
  const startLoggingBtn = document.getElementById('start-logging');
  const stopLoggingBtn = document.getElementById('stop-logging');
  const clearLoggingBtn = document.getElementById('clear-logging');

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

  const setpointDataset = {
    label: `Set-point (${SETPOINT.toFixed(1)} °C)`,
    borderColor: '#adb5bd',
    borderWidth: 1,
    borderDash: [6, 6],
    pointRadius: 0,
    tension: 0,
    spanGaps: true,
    data: [],
  };

  const valveDataset = {
    label: 'Valve (0/1)',
    borderColor: '#f4d35e',
    backgroundColor: 'rgba(244, 211, 94, 0.2)',
    borderWidth: 2,
    pointRadius: 0,
    tension: 0,
    stepped: true,
    yAxisID: 'valve',
    data: [],
  };

  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [...sensorDatasets, setpointDataset, valveDataset],
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
        valve: {
          position: 'right',
          min: -0.1,
          max: 1.1,
          ticks: {
            stepSize: 1,
            color: tickColor,
          },
          grid: {
            drawOnChartArea: false,
          },
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

  let ws = null;
  let reconnectDelay = 1000;
  let startEpochSec = null;
  const sensorSeries = Array.from({ length: MAX_SENSORS }, () => []);
  const setpointSeries = [];
  const valveSeries = [];

  let loggingEnabled = false;
  let loggingRows = [];

  function setConnectionStatus(text, tone = 'normal') {
    statusEl.textContent = `Status: ${text}`;
    statusEl.dataset.tone = tone;
  }

  function setLoggingStatus(text) {
    loggingStatusEl.textContent = `Logging: ${text}`;
  }

  function setCommandStatus(text, tone = 'normal') {
    commandStatusEl.textContent = text;
    commandStatusEl.dataset.tone = tone;
    if (text) {
      setTimeout(() => {
        commandStatusEl.textContent = '';
      }, 4000);
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

    pushSeries(setpointSeries, { x: tMin, y: SETPOINT });
    setpointDataset.data = setpointSeries;

    const valve = Number.isFinite(data.valve) ? Number(data.valve) : 0;
    pushSeries(valveSeries, { x: tMin, y: valve });
    valveDataset.data = valveSeries;

    const validValues = temps.slice(0, sensorCount).filter((v) => Number.isFinite(v));
    const avg = validValues.length
      ? validValues.reduce((acc, v) => acc + v, 0) / validValues.length
      : null;

    const validLabels = [];
    for (let i = 0; i < sensorCount; i += 1) {
      if (Number.isFinite(temps[i])) {
        validLabels.push(`U${i}`);
      }
    }

    avgTempEl.textContent = validValues.length ? `${avg.toFixed(2)} °C` : '—';
    sensorCountEl.textContent = `Active: ${sensorCount}`;
    validCountEl.textContent = `Valid now: ${validValues.length}`;
    validListEl.textContent = validLabels.length ? `Valid sensors: ${validLabels.join(', ')}` : 'Valid sensors: —';

    const valveOpen = Boolean(valve);
    valveStateEl.textContent = valveOpen ? 'OPEN' : 'CLOSED';
    valveStateEl.classList.toggle('valve-open', valveOpen);
    valveStateEl.classList.toggle('valve-closed', !valveOpen);

    const modeChar = typeof data.mode === 'string' ? data.mode.charAt(0).toUpperCase() : 'A';
    const modeText =
      modeChar === 'O'
        ? 'FORCED OPEN'
        : modeChar === 'C'
        ? 'FORCED CLOSE'
        : 'AUTO';
    modeStateEl.textContent = `Mode: ${modeText}`;

    if (loggingEnabled) {
      const row = [ts];
      for (let i = 0; i < MAX_SENSORS; i += 1) {
        row.push(Number.isFinite(temps[i]) ? temps[i] : 'nan');
      }
      row.push(valve);
      row.push(modeChar);
      loggingRows.push(row);
      setLoggingStatus(`on (${loggingRows.length} rows)`);
      clearLoggingBtn.disabled = false;
    }

    updateChartRanges();
    chart.update('none');
  }

  async function sendCommand(cmd) {
    try {
      setCommandStatus(`Sending "${cmd}"…`, 'info');
      const headers = {
        'Content-Type': 'application/json',
      };
      if (authHeaderValue) {
        headers.Authorization = authHeaderValue;
      }
      const response = await fetch('/api/command', {
        method: 'POST',
        headers,
        body: JSON.stringify({ cmd }),
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || response.statusText);
      }
      setCommandStatus(`Command "${cmd}" sent`, 'success');
    } catch (err) {
      console.error('Command error', err);
      setCommandStatus(`Command failed: ${err.message}`, 'error');
    }
  }

  function startLogging() {
    loggingEnabled = true;
    loggingRows = [];
    setLoggingStatus('on (0 rows)');
    startLoggingBtn.disabled = true;
    stopLoggingBtn.disabled = false;
    clearLoggingBtn.disabled = true;
  }

  function stopLogging(download = true) {
    if (!loggingEnabled) {
      return;
    }
    loggingEnabled = false;
    startLoggingBtn.disabled = false;
    stopLoggingBtn.disabled = true;
    setLoggingStatus('off');
    clearLoggingBtn.disabled = loggingRows.length === 0;

    if (download && loggingRows.length > 0) {
      downloadCsv();
    }
  }

  function clearLogging() {
    loggingRows = [];
    setLoggingStatus('off');
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
    setLoggingStatus('off');
  }

  document.querySelectorAll('button[data-cmd]').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      const cmd = event.currentTarget.getAttribute('data-cmd');
      if (cmd) {
        sendCommand(cmd);
      }
    });
  });

  startLoggingBtn.addEventListener('click', () => startLogging());
  stopLoggingBtn.addEventListener('click', () => stopLogging(true));
  clearLoggingBtn.addEventListener('click', () => {
    stopLogging(false);
    clearLogging();
  });

  window.addEventListener('beforeunload', () => {
    if (ws) {
      ws.close();
    }
  });

  connectWebSocket();
})();
