const state = {
  selectedChannel: 'a',
  status: null,
};

const $ = (id) => document.getElementById(id);

const channelSelect = $('channelSelect');
const waveSelect = $('waveSelect');
const enabledInput = $('enabledInput');
const amplitudeInput = $('amplitudeInput');
const freqInput = $('freqInput');
const phaseInput = $('phaseInput');
const dcInput = $('dcInput');
const arbBankSelect = $('arbBankSelect');
const expressionInput = $('expressionInput');
const presetSelect = $('presetSelect');
const freqDisplay = $('freqDisplay');
const displayMeta = $('displayMeta');
const displayExact = $('displayExact');
const displayFormula = $('displayFormula');
const logBox = $('logBox');
const loaderPill = $('loaderPill');
const bankPill = $('bankPill');
const coreId = $('coreId');
const coreVersion = $('coreVersion');
const sampleRate = $('sampleRate');
const lutLength = $('lutLength');
const coreStatus = $('coreStatus');
const dmaSummary = $('dmaSummary');
const dmaStatusRows = $('dmaStatusRows');
const channelStatus = $('channelStatus');

function log(message, level = 'info') {
  const prefix = level === 'error' ? '[ERROR]' : level === 'ok' ? '[OK]' : '[INFO]';
  const now = new Date();
  const ts = now.toLocaleTimeString('zh-CN', { hour12: false });
  logBox.textContent = `${prefix} ${ts}  ${message}`;
}

function formatIntegerHz(value) {
  const rounded = Math.max(0, Math.min(999999999, Math.round(Number(value) || 0)));
  return rounded.toString().padStart(9, '0').replace(/(\d{3})(\d{3})(\d{3})/, '$1.$2.$3');
}

function formatHumanHz(value) {
  return new Intl.NumberFormat('en-US').format(Math.round(Number(value) || 0));
}

function formatSigned(value, digits = 4) {
  return Number(value || 0).toFixed(digits);
}

function waveLabel(name) {
  return ({
    sine: 'sine',
    square: 'square',
    triangle: 'triangle',
    saw: 'sawtooth',
    sawtooth: 'sawtooth',
    arb: 'arbitrary waveform',
    arbitrary: 'arbitrary waveform',
  })[name] || name;
}

function selectedConfig() {
  if (!state.status) {
    return null;
  }
  return state.status.channels?.[state.selectedChannel] || null;
}

function row(key, value) {
  return `<div class="row"><div class="key">${key}</div><div class="value">${value}</div></div>`;
}

function renderDisplay() {
  const cfg = selectedConfig();
  if (!cfg) {
    return;
  }

  const freqHz = Number(cfg.freq_hz || 0);
  freqDisplay.value = formatIntegerHz(freqHz);
  displayMeta.textContent = `Channel ${state.selectedChannel.toUpperCase()} · ${waveLabel(cfg.wave_name)} · bank ${cfg.arb_bank}`;
  displayExact.textContent = `${formatHumanHz(freqHz)} Hz`;

  const lastExpression = state.status?.metadata?.last_expression?.[state.selectedChannel];
  displayFormula.textContent = cfg.wave_name === 'arb' && lastExpression
    ? `表达式模式：${lastExpression}`
    : '表达式模式未启用';
}

function renderStatus() {
  if (!state.status) {
    return;
  }

  const hw = state.status.hardware || {};
  const dma = state.status.dma_loader || {};

  coreId.textContent = `0x${Number(hw.id || 0).toString(16).toUpperCase().padStart(8, '0')}`;
  coreVersion.textContent = `0x${Number(hw.version || 0).toString(16).toUpperCase().padStart(8, '0')}`;
  sampleRate.textContent = `${formatHumanHz(hw.sample_rate_hz || 0)} Hz`;
  lutLength.textContent = `${formatHumanHz(hw.lut_length || 0)} points`;
  coreStatus.textContent = `0x${Number(hw.status || 0).toString(16).toUpperCase().padStart(8, '0')}`;

  const loaderState = dma.error ? `error:${dma.error_name}` : dma.busy ? 'busy' : dma.armed ? 'armed' : dma.done ? 'done' : 'idle';
  dmaSummary.textContent = loaderState;
  loaderPill.textContent = `DMA loader: ${loaderState}`;
  bankPill.textContent = `banks: A${hw.active_bank_a || 0} / B${hw.active_bank_b || 0}`;

  dmaStatusRows.innerHTML = [
    row('Loader target', `Channel ${String(dma.target_channel || 'a').toUpperCase()} · bank ${dma.target_bank ?? 0}`),
    row('Expected words', String(dma.expected_words ?? 0)),
    row('Received words', String(dma.received_words ?? 0)),
    row('Loader error code', `${dma.error_code ?? 0} (${dma.error_name || 'none'})`),
    row('MM2S DMASR', `0x${Number(dma.mm2s?.dmasr || 0).toString(16).toUpperCase().padStart(8, '0')}`),
    row('MM2S flags', `halted=${Boolean(dma.mm2s?.halted)} idle=${Boolean(dma.mm2s?.idle)} ioc_irq=${Boolean(dma.mm2s?.ioc_irq)} error=${Boolean(dma.mm2s?.error)}`),
  ].join('');

  const rows = [];
  for (const [channelName, cfg] of Object.entries(state.status.channels || {})) {
    rows.push(row(`Channel ${channelName.toUpperCase()} enabled`, cfg.enabled ? 'true' : 'false'));
    rows.push(row(`Channel ${channelName.toUpperCase()} wave`, waveLabel(cfg.wave_name)));
    rows.push(row(`Channel ${channelName.toUpperCase()} frequency`, `${formatHumanHz(cfg.freq_hz)} Hz`));
    rows.push(row(`Channel ${channelName.toUpperCase()} phase`, `${Number(cfg.phase_deg).toFixed(4)} deg`));
    rows.push(row(`Channel ${channelName.toUpperCase()} amplitude`, formatSigned(cfg.amplitude)));
    rows.push(row(`Channel ${channelName.toUpperCase()} DC offset`, formatSigned(cfg.dc_offset)));
    rows.push(row(`Channel ${channelName.toUpperCase()} active arb bank`, String(cfg.arb_bank)));
  }
  channelStatus.innerHTML = rows.join('');
}

function fillFormFromState() {
  const cfg = selectedConfig();
  if (!cfg) {
    return;
  }

  channelSelect.value = state.selectedChannel;
  waveSelect.value = cfg.wave_name;
  enabledInput.checked = Boolean(cfg.enabled);
  amplitudeInput.value = Number(cfg.amplitude).toString();
  freqInput.value = Math.round(Number(cfg.freq_hz || 0)).toString();
  phaseInput.value = Number(cfg.phase_deg || 0).toString();
  dcInput.value = Number(cfg.dc_offset || 0).toString();
  arbBankSelect.value = String(cfg.arb_bank ?? 0);
}

function payloadFromForm() {
  return {
    enabled: enabledInput.checked,
    wave_name: waveSelect.value,
    freq_hz: Number(freqInput.value),
    phase_deg: Number(phaseInput.value),
    amplitude: Number(amplitudeInput.value),
    dc_offset: Number(dcInput.value),
    arb_bank: Number(arbBankSelect.value),
  };
}

async function api(path, method = 'GET', body = null) {
  const options = {
    method,
    headers: {
      'Accept': 'application/json',
    },
  };

  if (body !== null) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`./api${path}`, options);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }

  return data;
}

async function refreshStatus() {
  const data = await api('/status');
  state.status = data;
  renderStatus();
  renderDisplay();
  fillFormFromState();
  log('成功获取 FPGA / AXI DMA / channel status。', 'ok');
}

async function applyChannelConfig() {
  const channel = state.selectedChannel;
  const payload = payloadFromForm();
  const data = await api(`/channel/${channel}/config`, 'POST', payload);
  state.status = data;
  renderStatus();
  renderDisplay();
  fillFormFromState();
  log(`Channel ${channel.toUpperCase()} shadow config applied to active registers。`, 'ok');
}

async function generateExpression() {
  const channel = state.selectedChannel;
  const expression = expressionInput.value.trim();
  if (!expression) {
    throw new Error('Expression is empty');
  }

  const payload = {
    expression,
    enabled: enabledInput.checked,
    freq_hz: Number(freqInput.value),
    phase_deg: Number(phaseInput.value),
    amplitude: Number(amplitudeInput.value),
    dc_offset: Number(dcInput.value),
    target_bank: Number(arbBankSelect.value),
    clear_phase: true,
  };

  const data = await api(`/channel/${channel}/expression`, 'POST', payload);
  state.status = data;
  renderStatus();
  renderDisplay();
  fillFormFromState();
  log(`Channel ${channel.toUpperCase()} expression LUT loaded via AXI DMA to bank ${data.target_bank}。`, 'ok');
}

function bindEvents() {
  channelSelect.addEventListener('change', () => {
    state.selectedChannel = channelSelect.value;
    fillFormFromState();
    renderDisplay();
  });

  presetSelect.addEventListener('change', () => {
    if (presetSelect.value) {
      expressionInput.value = presetSelect.value.replaceAll('^', '**');
    }
  });

  $('configForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await applyChannelConfig();
    } catch (error) {
      log(error.message, 'error');
    }
  });

  $('applyBtnTop').addEventListener('click', async () => {
    try {
      await applyChannelConfig();
    } catch (error) {
      log(error.message, 'error');
    }
  });

  $('expressionBtn').addEventListener('click', async () => {
    try {
      await generateExpression();
    } catch (error) {
      log(error.message, 'error');
    }
  });

  $('refreshBtn').addEventListener('click', async () => {
    try {
      await refreshStatus();
    } catch (error) {
      log(error.message, 'error');
    }
  });

  freqInput.addEventListener('input', () => {
    freqDisplay.value = formatIntegerHz(Number(freqInput.value));
    displayExact.textContent = `${formatHumanHz(freqInput.value)} Hz`;
  });

  waveSelect.addEventListener('change', () => {
    const mode = waveSelect.value === 'arb'
      ? '当前选择 arbitrary waveform，可直接下发表达式到指定 bank。'
      : `当前选择 ${waveLabel(waveSelect.value)}。`;
    displayFormula.textContent = mode;
  });
}

async function boot() {
  bindEvents();
  try {
    await refreshStatus();
  } catch (error) {
    log(error.message, 'error');
  }
}

boot();
