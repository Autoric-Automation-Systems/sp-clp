const machines = document.querySelector('#machines');
const empty = document.querySelector('#empty');
const refreshState = document.querySelector('#refresh-state');
const dialog = document.querySelector('#settings-dialog');
const message = document.querySelector('#settings-message');
const sessionState = document.querySelector('#session-state');
const dashboardPage = document.querySelector('#dashboard-page');
const plantPage = document.querySelector('#plant-page');
const plantTitle = document.querySelector('#plant-title');
const plantMachines = document.querySelector('#plant-machines');
const plantEmpty = document.querySelector('#plant-empty');
const settingsPage = document.querySelector('#settings-page');
const helpPage = document.querySelector('#help-page');
const settingsLocked = document.querySelector('#settings-locked');
const settingsContent = document.querySelector('#settings-content');
const machineForm = document.querySelector('#machine-form');
const menu = document.querySelector('#main-menu');
const menuBackdrop = document.querySelector('#menu-backdrop');
let token = null;
let editingMachineId = null;
let editingAreaId = null;
let editingLabelsFor = null;
let editorSignals = [];
let cachedMachines = [];
let cachedAreas = [];
let lastItems = [];
const signalsOpen = new Set();

// Static <svg data-icon="..."> placeholders in index.html are filled from icons.js.
// Templates rendered by this file call icon() directly.
hydrateIcons();

async function request(url, options = {}) {
  const headers = {...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(url, {...options, headers});
}

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function stateIcon(state) {
  if (state === 'ok') return 'circle-check';
  if (state === 'bad') return 'circle-x';
  return 'circle-help';
}

// Each bit means something different, so the wording and the colour both follow
// the meaning instead of the raw bit value.
function signalIcon(item) {
  if (item.kind === 'fault') return 'triangle-alert';
  if (item.kind === 'safety') return 'shield-check';
  if (item.kind === 'auto') return 'power';
  if (item.kind === 'run') return 'play';
  if (item.kind === 'counter') return 'gauge';
  return 'tag';
}

// Index 0 is the bit cleared, index 1 is the bit set.
const BIT_WORDS = {
  auto: ['Manual', 'Automático'],
  run: ['Parado', 'Produzindo'],
  fault: ['Normal', 'Em falha'],
  safety: ['Pendente', 'Normal'],
  // A diagnostic: the logic only counts on the rising edge, so this says whether
  // counting is happening at all, not how much was counted.
  counter: ['Sem contagem', 'Contando'],
};

// FAULT is the odd one out: the bit is set while the machine is in fault, so a
// set bit is the bad state and a cleared bit is the healthy one.
const INVERTED_BITS = new Set(['fault']);

function isHealthy(item) {
  return INVERTED_BITS.has(item.kind) ? !item.value : Boolean(item.value);
}

function stateOf(item) {
  // A null value means the PLC could not be read, which is not the same as a false bit.
  if (!item || item.value === null || item.value === undefined) return 'unknown';
  // A number has no healthy or faulty state, only a value.
  if (item.type === 'DINT') return 'value';
  return isHealthy(item) ? 'ok' : 'bad';
}

function stateText(item, state) {
  if (state === 'unknown') return 'Sem leitura';
  if (state === 'value') return String(item.value);
  const words = BIT_WORDS[item.kind];
  // A free signal has no agreed vocabulary, so it shows the bit itself: the
  // operator reads 1 or 0 and knows exactly what the PLC is holding.
  if (!words) return item.value ? '1 LIGADO' : '0 DESLIGADO';
  return item.value ? words[1] : words[0];
}

// The hue of each standard signal when its bit is set. The colour is a second
// channel, never the only one: the shape of the icon and the word below it say the
// same thing, so colour blindness or a dirty screen does not hide a state.
const STATE_HUES = {
  auto: 'blue',
  run: 'green',
  fault: 'red',
  safety: 'green',
  counter: 'cyan',
};

function statusColour(item, state) {
  // A lost link must never look like a bit at zero, or a card reads as a stopped
  // machine when the panel simply cannot see the PLC.
  if (state === 'unknown') return 'unknown';
  // The stylesheet keys the colour on the "on-" prefix, so the hue is only the
  // second half of the class name. Without it nothing matches and every block
  // falls back to the neutral default, which is what a grey card looks like.
  return item.value ? `on-${STATE_HUES[item.kind] || 'blue'}` : 'off';
}

function statusTooltip(item, state) {
  if (state === 'unknown') return `${item.label} (${item.address}) - sem leitura do CLP`;
  // The word that used to sit under the icon lives here now, so the vocabulary is
  // still reachable without a label taking room on the card.
  return `${item.label} (${item.address}) - bit ${item.value ? 1 : 0} - ${stateText(item, state)}`;
}

function statusBlock(item) {
  if (!item) return '';
  // Only the icon is drawn: its shape says which signal and its colour says the
  // state. The wording waits on hover, and an accessible label carries it for
  // whoever cannot hover.
  const state = stateOf(item);
  const tip = statusTooltip(item, state);
  return `<div class="state ${statusColour(item, state)}" title="${esc(tip)}" aria-label="${esc(tip)}"><span class="state-icon">${icon(signalIcon(item))}</span></div>`;
}

function signalsOfKind(status, kind) {
  return (status.signals || []).filter(function (item) { return item.kind === kind; });
}

function firstOfKind(status, kind) {
  return signalsOfKind(status, kind)[0] || null;
}

function hourlySection(machine) {
  // The totals live in a window of their own, so this button only opens it.
  return `<div class="hourly"><button type="button" class="chart-open" data-chart-id="${machine.id}"><span class="toggle-label">${icon('clock')}Contagens por hora</span>${icon('arrow-right')}</button></div>`;
}

// Everything that has no block of its own: the free BOOLs and the three DInts.
// The five standard signals are all shown as blocks above.
function listedSignals(status) {
  return (status.signals || []).filter(function (item) {
    return item.kind === 'custom' || item.kind === 'integer';
  });
}

function signalRow(item) {
  const state = stateOf(item);
  const text = esc(state === 'unknown' ? '-' : stateText(item, state));
  // A number needs no verdict, so it is shown alone instead of as a state chip.
  const badge = state === 'value'
    ? `<span class="signal-state value">${text}</span>`
    : `<span class="signal-state ${state}">${icon(stateIcon(state))}${text}</span>`;
  return `<li class="signal-row"><span class="signal-address">${esc(item.address)}</span><span class="signal-name">${esc(item.label)}</span>${badge}</li>`;
}

function signalsSection(machine, status) {
  const items = listedSignals(status);
  const open = signalsOpen.has(machine.id);
  let body = '';
  if (open) {
    body = '<ul class="signal-list">' + items.map(signalRow).join('') + '</ul>';
  }
  const action = open ? 'Ocultar' : 'Ver';
  return `<div class="signals"><button type="button" class="signals-toggle" data-signals-id="${machine.id}" aria-expanded="${open}"><span class="toggle-label">${icon('list')}${action} sinais (${items.length})</span>${icon(open ? 'chevron-up' : 'chevron-down')}</button>${body}</div>`;
}

function machineCard(machine, status) {
  // The counter label is part of the contract, so the block names it directly.
  const connection = status.connected ? 'online' : 'offline';
  return `<article class="machine-card"><div class="machine-card-head"><div><span class="machine-kicker">${icon('cpu')}DB${esc(machine.db_number)}</span><h3>${esc(machine.name)}</h3><p class="meta">${esc(machine.ip)}</p></div><span class="connection-pill ${connection}">${icon(connection === 'online' ? 'wifi' : 'wifi-off')}${status.connected ? 'Online' : 'Offline'}</span></div><div class="state-grid">${statusBlock(firstOfKind(status, 'auto'))}${statusBlock(firstOfKind(status, 'run'))}${statusBlock(firstOfKind(status, 'fault'))}${statusBlock(firstOfKind(status, 'safety'))}${statusBlock(firstOfKind(status, 'counter'))}</div><div class="count"><span class="count-label">${icon('gauge')}CONTADOR ATUAL</span><b>${status.count ?? '-'}</b><span>${status.timestamp ? 'Atualizado às ' + new Date(status.timestamp).toLocaleTimeString() : 'Aguardando leitura do CLP'}</span></div>${signalsSection(machine, status)}${hourlySection(machine)}</article>`;
}

function renderAreas(items) {
  const areas = new Map();
  items.forEach(function (entry) {
    const area = entry[0].area_name || 'Área sem nome';
    if (!areas.has(area)) areas.set(area, []);
    areas.get(area).push(machineCard(entry[0], entry[1]));
  });
  return Array.from(areas.entries()).map(function (group) {
    const cards = group[1];
    const label = cards.length === 1 ? '1 máquina' : cards.length + ' máquinas';
    return `<div class="area-group"><div class="area-heading"><span class="area-mark">${icon('layers')}</span><h4>${esc(group[0])}</h4><span>${esc(label)}</span></div><div class="machine-grid">${cards.join('')}</div></div>`;
  }).join('');
}

function overviewMachine(entry) {
  const machine = entry[0];
  const connected = Boolean(entry[1].connected);
  const state = connected ? 'online' : 'offline';
  return `<li class="overview-machine"><span class="overview-machine-state ${state}">${icon(connected ? 'wifi' : 'wifi-off')}</span><span class="overview-machine-name">${esc(machine.name)}</span><span class="overview-machine-area">${esc(machine.area_name || '')}</span><span class="overview-machine-status ${state}">${connected ? 'Online' : 'Offline'}</span></li>`;
}

// The dashboard is a single card: one block per plant, each machine showing only
// whether the CLP answered. The block links to the plant address for the detail.
function overviewPlant(plant) {
  const online = plant.machines.filter(function (entry) { return entry[1].connected; }).length;
  const total = plant.machines.length;
  const head = `<div class="overview-plant-head"><span class="overview-plant-name">${icon('factory')}${esc(plant.name)}</span>${plant.slug ? `<span class="overview-plant-address">/${esc(plant.slug)}</span>` : ''}</div><span class="overview-plant-count">${online} de ${total} online</span><ul class="overview-machines">${plant.machines.map(overviewMachine).join('')}</ul>`;
  if (!plant.slug) return `<div class="overview-plant static">${head}</div>`;
  const route = '/' + plant.slug;
  return `<a class="overview-plant" href="/${esc(plant.slug)}" data-route="${esc(route)}">${head}<span class="overview-plant-go">${icon('arrow-right')}</span></a>`;
}

function overviewCard(items) {
  const plants = new Map();
  items.forEach(function (entry) {
    const machine = entry[0];
    const name = machine.plant_name || 'Planta sem nome';
    if (!plants.has(name)) plants.set(name, {name: name, slug: machine.plant_slug || '', machines: []});
    plants.get(name).machines.push(entry);
  });
  return `<article class="machine-card overview">${Array.from(plants.values()).map(overviewPlant).join('')}</article>`;
}

async function refresh() {
  const response = await request('/api/machines');
  cachedMachines = await response.json();
  empty.hidden = cachedMachines.length > 0;
  const items = await Promise.all(cachedMachines.map(async function (machine) {
    const statusResponse = await request('/api/machines/' + machine.id + '/status');
    return [machine, await statusResponse.json()];
  }));
  lastItems = items;
  // The plant route can only be resolved once the machine list is known.
  applyRoute();
  refreshState.innerHTML = icon('refresh-cw') + 'Atualizado às ' + new Date().toLocaleTimeString();
}

function bindToggles() {
  document.querySelectorAll('.chart-open').forEach(function (button) {
    button.onclick = function () { openChart(Number(button.dataset.chartId)); };
  });
  document.querySelectorAll('.signals-toggle').forEach(function (button) {
    button.onclick = function () { toggleSignals(Number(button.dataset.signalsId)); };
  });
}

function renderPlantPage(plant) {
  const items = lastItems.filter(function (entry) {
    return String(entry[0].plant_slug || '').toLowerCase() === plant.slug.toLowerCase();
  });
  plantTitle.textContent = plant.name;
  plantMachines.innerHTML = renderAreas(items);
  plantEmpty.hidden = items.length > 0;
  bindToggles();
}

// Every caller repaints "the current page", so the plant route is handled here.
function renderCards() {
  const plant = currentPlant();
  if (plant) {
    renderPlantPage(plant);
    return;
  }
  machines.innerHTML = overviewCard(lastItems);
  bindToggles();
}

function toggleSignals(machineId) {
  if (signalsOpen.has(machineId)) {
    signalsOpen.delete(machineId);
  } else {
    signalsOpen.add(machineId);
  }
  renderCards();
}

// Hourly totals open a small window with one bar per hour of a chosen day.
let chartMachine = null;
let chartDay = null;
let chartData = null;

function shiftDay(day, delta) {
  const moment = new Date(day + 'T12:00:00Z');
  moment.setUTCDate(moment.getUTCDate() + delta);
  return moment.toISOString().slice(0, 10);
}

function formatDay(day, today) {
  const stamp = day.slice(8, 10) + '/' + day.slice(5, 7) + '/' + day.slice(0, 4);
  return day === today ? stamp + ' (hoje)' : stamp;
}

function chartMarkup(data) {
  const slots = data.slots || [];
  const highest = Math.max(1, ...slots.map(function (slot) { return slot.quantity; }));
  const width = 760;
  const height = 260;
  const left = 46;
  const right = 12;
  const top = 16;
  const bottom = 28;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const step = plotWidth / Math.max(1, slots.length);
  const barWidth = Math.max(3, step * 0.62);
  let body = '';
  for (let line = 0; line <= 4; line += 1) {
    const y = top + (plotHeight / 4) * line;
    body += `<line class="chart-grid" x1="${left}" x2="${width - right}" y1="${y}" y2="${y}"/>`;
    body += `<text class="chart-axis" x="${left - 8}" y="${y + 4}" text-anchor="end">${Math.round(highest - (highest / 4) * line)}</text>`;
  }
  slots.forEach(function (slot, index) {
    const barHeight = (slot.quantity / highest) * plotHeight;
    const x = left + step * index + (step - barWidth) / 2;
    const y = top + plotHeight - barHeight;
    const hour = slot.local_hour.slice(11, 13);
    body += `<rect class="chart-bar${slot.quantity ? '' : ' empty'}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${Math.max(0, barHeight).toFixed(1)}" rx="2"><title>${hour}:00 — ${slot.quantity}</title></rect>`;
    if (index % 3 === 0) {
      body += `<text class="chart-axis" x="${(left + step * index + step / 2).toFixed(1)}" y="${height - 8}" text-anchor="middle">${hour}</text>`;
    }
  });
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Contagens por hora em ${esc(data.day)}">${body}</svg>`;
}

function renderChart() {
  const dialogTitle = document.querySelector('#chart-title');
  dialogTitle.textContent = chartMachine ? chartMachine.name : 'Contagens';
  const body = document.querySelector('#chart-body');
  if (!chartData) {
    body.innerHTML = '';
    return;
  }
  const total = chartData.slots.reduce(function (sum, slot) { return sum + slot.quantity; }, 0);
  document.querySelector('#chart-day').textContent = formatDay(chartData.day, chartData.today);
  document.querySelector('#chart-total').textContent = total === 1 ? '1 caixa no dia' : total + ' caixas no dia';
  body.innerHTML = chartMarkup(chartData);
  document.querySelector('#chart-prev').disabled = chartData.day <= chartData.first_day;
  document.querySelector('#chart-next').disabled = chartData.day >= chartData.last_day;
}

async function loadChart() {
  const query = chartDay ? '?day=' + encodeURIComponent(chartDay) : '';
  const response = await request('/api/machines/' + chartMachine.id + '/hourly-counts' + query);
  const target = document.querySelector('#chart-message');
  if (!response.ok) {
    target.textContent = 'Não foi possível carregar as contagens deste dia.';
    return;
  }
  target.textContent = '';
  chartData = await response.json();
  chartDay = chartData.day;
  renderChart();
}

async function openChart(machineId) {
  const machine = cachedMachines.find(function (item) { return item.id === machineId; });
  if (!machine) return;
  chartMachine = machine;
  chartDay = null;
  chartData = null;
  document.querySelector('#chart-body').innerHTML = '';
  document.querySelector('#chart-dialog').showModal();
  await loadChart();
}

function stepChart(delta) {
  if (!chartData) return;
  chartDay = shiftDay(chartData.day, delta);
  loadChart();
}

async function setupStatus() {
  const status = await (await request('/api/setup/status')).json();
  document.querySelector('#setup-fields').hidden = status.password_configured;
  document.querySelector('#login-fields').hidden = !status.password_configured;
}

function setMenu(open) {
  menu.classList.toggle('open', open);
  menuBackdrop.classList.toggle('visible', open);
  document.querySelector('#menu-toggle').setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-hidden', String(!open));
}

// Routing. Every plant answers on its own address, so the path decides the page.
const SETTINGS_SEGMENT = 'configuracoes';
const HELP_SEGMENT = 'ajuda';

function decodeSegment(pathname) {
  return decodeURIComponent(pathname.replace(/^\/+|\/+$/g, '')).toLowerCase();
}

function currentPath() {
  return location.pathname.replace(/\/+$/, '') || '/';
}

function currentPlant() {
  const segment = decodeSegment(location.pathname);
  if (!segment || segment === SETTINGS_SEGMENT || segment === HELP_SEGMENT) return null;
  const machine = cachedMachines.find(function (item) {
    return String(item.plant_slug || '').toLowerCase() === segment;
  });
  return machine ? {name: machine.plant_name, slug: machine.plant_slug} : null;
}

function routeTitle(settings, help) {
  if (settings) return '/Configuracoes';
  if (help) return '/Ajuda';
  return '/';
}

function applyRoute(options) {
  const segment = decodeSegment(location.pathname);
  const settings = segment === SETTINGS_SEGMENT;
  const help = segment === HELP_SEGMENT;
  const plant = settings || help ? null : currentPlant();
  const dashboard = !settings && !help && !plant;

  dashboardPage.hidden = !dashboard;
  plantPage.hidden = !plant;
  settingsPage.hidden = !settings;
  helpPage.hidden = !help;

  const active = routeTitle(settings, help);
  document.querySelectorAll('.nav-button').forEach(function (button) {
    button.classList.toggle('active', button.dataset.route === active);
  });

  renderCards();
  if (settings && options && options.settings) loadSettings();
}

function navigate(path, options) {
  if (path !== currentPath()) history.pushState({}, '', path);
  setMenu(false);
  applyRoute(options);
}

function machineListItem(machine) {
  return `<div class="machine-list-item"><span>${esc(machine.name)} - ${esc(machine.ip)} - DB${esc(machine.db_number)}</span><span class="machine-actions"><button type="button" class="labels-machine with-icon" data-machine-id="${machine.id}">${icon('tag')}Sinais</button><button type="button" class="edit-machine with-icon secondary" data-machine-id="${machine.id}">${icon('pencil')}Editar</button><button type="button" class="delete-machine danger with-icon" data-machine-id="${machine.id}">${icon('trash-2')}Excluir</button></span></div>`;
}

function areaListItem(area, machines) {
  const label = machines.length === 1 ? '1 máquina' : machines.length + ' máquinas';
  const rows = machines.map(machineListItem).join('') || '<p class="form-hint">Nenhuma máquina nesta área.</p>';
  return `<div class="area-item"><div class="list-item"><strong>${esc(area.name)}</strong><span class="list-actions"><span class="list-meta">${esc(label)}</span><button type="button" class="edit-area secondary with-icon" data-area-id="${area.id}">${icon('pencil')}Editar</button><button type="button" class="delete-area danger with-icon" data-area-id="${area.id}">${icon('trash-2')}Excluir</button></span></div>${rows}</div>`;
}

function plantListItem(plant, machinesByArea) {
  const areas = plant.areas.map(function (area) {
    return areaListItem(area, machinesByArea.get(area.id) || []);
  }).join('');
  const rename = plant.slug
    ? `<button type="button" class="rename-plant secondary with-icon" data-plant-slug="${esc(plant.slug)}" data-plant-name="${esc(plant.name)}">${icon('pencil')}Renomear planta</button>`
    : '';
  const address = plant.slug ? `/${esc(plant.slug)}` : 'sem endereço';
  return `<div class="plant-item"><div class="list-item"><strong>${esc(plant.name)}</strong><span class="list-actions"><span class="list-meta">${esc(address)}</span>${rename}</span></div>${areas}</div>`;
}

function groupAreasByPlant(areas, machines) {
  const machinesByArea = new Map();
  machines.forEach(function (machine) {
    if (!machinesByArea.has(machine.area_id)) machinesByArea.set(machine.area_id, []);
    machinesByArea.get(machine.area_id).push(machine);
  });
  const plants = new Map();
  areas.forEach(function (area) {
    if (!plants.has(area.plant_name)) {
      plants.set(area.plant_name, {name: area.plant_name, slug: area.plant_slug || '', areas: []});
    }
    plants.get(area.plant_name).areas.push(area);
  });
  return Array.from(plants.values()).map(function (plant) {
    return plantListItem(plant, machinesByArea);
  }).join('');
}

function applyBranding(branding) {
  const name = (branding && branding.company_name) || 'SP-CLP';
  document.querySelector('#brand-name').textContent = name;
  const image = document.querySelector('#brand-logo');
  image.src = (branding && branding.logo_url) || '/static/assets/favicon_io/android-chrome-192x192.png';
  image.alt = name;
  document.title = name + ' | Monitoramento';
}

async function loadAccess() {
  const response = await request('/api/access');
  if (!response.ok) return;
  const info = await response.json();
  const list = document.querySelector('#access-addresses');
  if (list) {
    list.innerHTML = info.urls.map(function (url) {
      return `<li><a href="${esc(url)}">${esc(url)}</a></li>`;
    }).join('');
  }
  const note = document.querySelector('#access-alias');
  if (!note) return;
  note.classList.toggle('ready', Boolean(info.alias_ready));
  note.textContent = info.alias_ready
    ? `O apelido ${info.alias} já responde neste computador: ${info.alias_url}`
    : `O apelido ${info.alias} ainda não responde neste computador; por isso ele não aparece na lista acima.`;
}

async function loadBranding() {
  const response = await request('/api/branding');
  if (response.ok) applyBranding(await response.json());
}

function formatBytes(size) {
  if (size === null || size === undefined) return '';
  if (size < 1024) return size + ' B';
  if (size < 1024 * 1024) return Math.round(size / 1024) + ' KB';
  return (size / (1024 * 1024)).toFixed(1).replace('.', ',') + ' MB';
}

// A build may carry no block, and there may be more than one family, so the help
// page lists whatever is really there instead of pointing at a fixed name.
async function loadLibrary() {
  const target = document.querySelector('#library-files');
  if (!target) return;
  const response = await request('/api/library');
  if (!response.ok) return;
  const info = await response.json();
  const files = info.files || [];
  if (files.length === 0) {
    target.innerHTML = `<li class="library-empty">${icon('info')}A biblioteca do bloco não está nesta instalação do painel.</li>`;
    return;
  }
  target.innerHTML = files.map(function (item) {
    return `<li class="library-row"><span class="library-family">${esc(item.label)}</span>`
      + `<a class="download-link with-icon" href="${esc(item.url)}" download>${icon('save')}${esc(item.filename)}</a>`
      + `<span class="library-size">${esc(formatBytes(item.size_bytes))}</span></li>`;
  }).join('');
}

async function showBrandingMessage(response, okText) {
  const target = document.querySelector('#branding-message');
  if (response.ok) {
    target.textContent = okText;
    applyBranding(await response.json());
    return true;
  }
  const body = await response.json().catch(function () { return {}; });
  target.textContent = typeof body.detail === 'string' ? body.detail : 'Não foi possível salvar a identidade.';
  return false;
}

// The header frame is a fixed square, so a wide logo has to be framed by hand.
const CROP_SIZE = 256;
let cropImage = null;
let cropZoom = 1;
let cropOffset = {x: 0, y: 0};
let cropDrag = null;

function cropCanvas() {
  return document.querySelector('#crop-canvas');
}

function cropCover() {
  if (!cropImage) return 1;
  return Math.max(CROP_SIZE / cropImage.width, CROP_SIZE / cropImage.height);
}

function drawCrop() {
  const canvas = cropCanvas();
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, CROP_SIZE, CROP_SIZE);
  if (!cropImage) return;
  const scale = cropCover() * cropZoom;
  const width = cropImage.width * scale;
  const height = cropImage.height * scale;
  // Keep the picture covering the frame: no empty band may show through.
  cropOffset.x = Math.min(0, Math.max(CROP_SIZE - width, cropOffset.x));
  cropOffset.y = Math.min(0, Math.max(CROP_SIZE - height, cropOffset.y));
  context.drawImage(cropImage, cropOffset.x, cropOffset.y, width, height);
}

function centerCrop() {
  const scale = cropCover() * cropZoom;
  cropOffset.x = (CROP_SIZE - cropImage.width * scale) / 2;
  cropOffset.y = (CROP_SIZE - cropImage.height * scale) / 2;
}

function setCropZoom(value) {
  const scaleBefore = cropCover() * cropZoom;
  // Anchor the zoom on the middle of the frame instead of the corner.
  const anchorX = (CROP_SIZE / 2 - cropOffset.x) / scaleBefore;
  const anchorY = (CROP_SIZE / 2 - cropOffset.y) / scaleBefore;
  cropZoom = value;
  const scaleAfter = cropCover() * cropZoom;
  cropOffset.x = CROP_SIZE / 2 - anchorX * scaleAfter;
  cropOffset.y = CROP_SIZE / 2 - anchorY * scaleAfter;
  drawCrop();
}

function openCropTool(file) {
  const image = new Image();
  image.onload = function () {
    cropImage = image;
    cropZoom = 1;
    document.querySelector('#crop-zoom').value = '1';
    centerCrop();
    drawCrop();
    document.querySelector('#logo-message').textContent = '';
    document.querySelector('#logo-dialog').showModal();
  };
  image.onerror = function () {
    document.querySelector('#branding-message').textContent = 'Não foi possível abrir a imagem.';
  };
  image.src = URL.createObjectURL(file);
}

document.querySelector('#crop-canvas').addEventListener('pointerdown', function (event) {
  if (!cropImage) return;
  cropDrag = {x: event.clientX, y: event.clientY, offsetX: cropOffset.x, offsetY: cropOffset.y};
  event.target.setPointerCapture(event.pointerId);
});

document.querySelector('#crop-canvas').addEventListener('pointermove', function (event) {
  if (!cropDrag) return;
  const box = event.target.getBoundingClientRect();
  const ratio = CROP_SIZE / box.width;
  cropOffset.x = cropDrag.offsetX + (event.clientX - cropDrag.x) * ratio;
  cropOffset.y = cropDrag.offsetY + (event.clientY - cropDrag.y) * ratio;
  drawCrop();
});

document.querySelector('#crop-canvas').addEventListener('pointerup', function () {
  cropDrag = null;
});

document.querySelector('#crop-zoom').oninput = function (event) {
  if (!cropImage) return;
  setCropZoom(Number(event.target.value) || 1);
};

document.querySelector('#company-logo').onchange = function (event) {
  const file = event.target.files[0];
  if (file) openCropTool(file);
};

document.querySelector('#logo-apply').onclick = async function () {
  const cropped = cropCanvas().toDataURL('image/png').split(',')[1] || '';
  const response = await request('/api/config/branding/logo', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({filename: 'logo.png', content: cropped}),
  });
  const ok = await showBrandingMessage(response, 'Logotipo atualizado.');
  document.querySelector('#logo-message').textContent = ok ? '' : document.querySelector('#branding-message').textContent;
  if (ok) {
    document.querySelector('#company-logo').value = '';
    document.querySelector('#logo-dialog').close();
  }
};

document.querySelector('#logo-cancel').onclick = function () {
  document.querySelector('#company-logo').value = '';
  document.querySelector('#logo-dialog').close();
};

document.querySelector('#logo-close').onclick = function () {
  document.querySelector('#company-logo').value = '';
  document.querySelector('#logo-dialog').close();
};

function closeChart() {
  document.querySelector('#chart-dialog').close();
}

// A closed dialog keeps the old markup, so drop the day when it goes away.
for (const id of ['chart-close', 'chart-close-action']) {
  document.querySelector('#' + id).onclick = closeChart;
}
document.querySelector('#chart-prev').onclick = function () { stepChart(-1); };
document.querySelector('#chart-next').onclick = function () { stepChart(1); };

document.querySelector('#branding-form').onsubmit = async function (event) {
  event.preventDefault();
  const name = await request('/api/config/branding', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({company_name: document.querySelector('#company-name').value}),
  });
  await showBrandingMessage(name, 'Identidade salva.');
};

document.querySelector('#branding-logo-remove').onclick = async function () {
  const response = await request('/api/config/branding/logo', {method: 'DELETE'});
  await showBrandingMessage(response, 'Logotipo removido.');
};

async function loadSettings() {
  if (!token) return;
  settingsLocked.hidden = true;
  settingsContent.hidden = false;
  const response = await request('/api/config/areas');
  if (!response.ok) return;
  cachedAreas = await response.json();
  const branding = await request('/api/branding');
  if (branding.ok) {
    const info = await branding.json();
    document.querySelector('#company-name').value = info.company_name || '';
  }
  document.querySelector('#area-select').innerHTML = cachedAreas.length
    ? cachedAreas.map(function (area) { return `<option value="${area.id}">${esc(area.plant_name)} / ${esc(area.name)}</option>`; }).join('')
    : '<option value="">Crie uma área primeiro</option>';

  const withSlug = cachedAreas.filter(function (area) { return area.plant_slug; });
  const seen = new Set();
  const plants = withSlug.filter(function (area) {
    if (seen.has(area.plant_slug)) return false;
    seen.add(area.plant_slug);
    return true;
  });
  const plantSelect = document.querySelector('#plant-select');
  plantSelect.innerHTML = plants.length
    ? plants.map(function (area) { return `<option value="${esc(area.plant_slug)}">${esc(area.plant_name)}</option>`; }).join('')
    : '<option value="">Nenhuma planta com endereço</option>';
  document.querySelector('#plant-submit').disabled = plants.length === 0;

  document.querySelector('#area-list').innerHTML = cachedAreas.length
    ? groupAreasByPlant(cachedAreas, cachedMachines)
    : '<p class="form-hint">Nenhuma área cadastrada.</p>';

  document.querySelectorAll('.edit-machine').forEach(function (button) {
    button.onclick = function () { beginEdit(Number(button.dataset.machineId)); };
  });
  document.querySelectorAll('.labels-machine').forEach(function (button) {
    button.onclick = function () { openSignalLabels(Number(button.dataset.machineId)); };
  });
  document.querySelectorAll('.delete-machine').forEach(function (button) {
    button.onclick = function () { deleteMachine(Number(button.dataset.machineId)); };
  });
  document.querySelectorAll('.edit-area').forEach(function (button) {
    button.onclick = function () { beginAreaEdit(Number(button.dataset.areaId)); };
  });
  document.querySelectorAll('.delete-area').forEach(function (button) {
    button.onclick = function () { deleteArea(Number(button.dataset.areaId)); };
  });
  document.querySelectorAll('.rename-plant').forEach(function (button) {
    button.onclick = function () { beginPlantRename(button.dataset.plantSlug, button.dataset.plantName); };
  });
}

function beginAreaEdit(areaId) {
  const area = cachedAreas.find(function (item) { return item.id === areaId; });
  if (!area) return;
  editingAreaId = areaId;
  document.querySelector('#plant-name').value = area.plant_name;
  document.querySelector('#plant-name').disabled = true;
  document.querySelector('#area-name').value = area.name;
  document.querySelector('#area-form-eyebrow').textContent = 'EDITAR ÁREA';
  document.querySelector('#area-form-title').textContent = 'Atualizar área';
  document.querySelector('#area-submit-label').textContent = 'Salvar alterações';
  document.querySelector('#area-cancel').hidden = false;
  document.querySelector('#area-form').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function cancelAreaEdit() {
  editingAreaId = null;
  document.querySelector('#area-form').reset();
  document.querySelector('#plant-name').disabled = false;
  document.querySelector('#area-form-eyebrow').textContent = 'NOVA ÁREA';
  document.querySelector('#area-form-title').textContent = 'Adicionar área';
  document.querySelector('#area-submit-label').textContent = 'Salvar área';
  document.querySelector('#area-cancel').hidden = true;
}

async function deleteArea(areaId) {
  const area = cachedAreas.find(function (item) { return item.id === areaId; });
  if (!area || !confirm('Excluir a área "' + area.name + '"?')) return;
  const response = await request('/api/config/areas/' + areaId, {method: 'DELETE'});
  const target = document.querySelector('#area-message');
  const body = await response.json().catch(function () { return {}; });
  target.textContent = response.ok ? 'Área excluída.' : (body.detail || 'Não foi possível excluir a área.');
  if (response.ok) { cancelAreaEdit(); await refresh(); loadSettings(); }
}

function beginPlantRename(slug, name) {
  document.querySelector('#plant-select').value = slug;
  const field = document.querySelector('#plant-rename');
  field.value = name;
  document.querySelector('#plant-form').scrollIntoView({behavior: 'smooth', block: 'start'});
  field.focus();
}

const PROBE_ICONS = {
  ready: 'circle-check',
  unsigned: 'triangle-alert',
  unreachable: 'wifi-off',
};

function showProbe(status, message) {
  const target = document.querySelector('#machine-probe-message');
  target.className = 'probe-message ' + status;
  target.innerHTML = icon(PROBE_ICONS[status] || 'circle-help') + esc(message);
  target.hidden = false;
}

function resetProbe() {
  const target = document.querySelector('#machine-probe-message');
  target.hidden = true;
  target.textContent = '';
  const list = document.querySelector('#machine-scan-databases');
  list.innerHTML = '';
  list.hidden = true;
}

function highlightDatabase() {
  const value = Number(document.querySelector('#machine-db').value);
  document.querySelectorAll('.db-chip').forEach(function (chip) {
    chip.classList.toggle('selected', Number(chip.dataset.db) === value);
  });
}

// The found DBs are buttons: typing the number by hand is what the sweep exists
// to avoid, so selecting one is the only way the field is meant to be filled.
function renderScanDatabases(databases) {
  const list = document.querySelector('#machine-scan-databases');
  list.innerHTML = '';
  databases.forEach(function (number) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'db-chip';
    chip.dataset.db = String(number);
    chip.textContent = 'DB ' + number;
    chip.onclick = function () {
      document.querySelector('#machine-db').value = String(number);
      highlightDatabase();
      showProbe('ready', 'DB ' + number + ' selecionada. Ela já está no campo DB.');
    };
    list.appendChild(chip);
  });
  list.hidden = databases.length === 0;
  highlightDatabase();
}

// Read only: the DB numbers are swept on the PLC before the machine is saved, so
// a number nobody typed is what ends up configured.
async function scanMachine() {
  const button = document.querySelector('#machine-probe');
  const label = document.querySelector('#machine-probe-label');
  const ip = document.querySelector('#machine-ip').value.trim();
  if (!ip) {
    showProbe('unreachable', 'Informe o IP do CLP antes de procurar.');
    return;
  }
  button.disabled = true;
  label.textContent = 'Procurando...';
  resetProbe();
  try {
    const response = await request('/api/config/plc/scan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ip: ip}),
    });
    const body = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      showProbe('unreachable', typeof body.detail === 'string' ? body.detail : 'Não foi possível varrer o CLP.');
      return;
    }
    showProbe(body.status, body.message);
    renderScanDatabases(body.databases || []);
  } catch (error) {
    showProbe('unreachable', 'Sem resposta do servidor do painel. Confira se o SP-CLP continua aberto.');
  } finally {
    button.disabled = false;
    label.textContent = 'Procurar DB';
  }
}

function beginEdit(machineId) {
  const machine = cachedMachines.find(function (item) { return item.id === machineId; });
  if (!machine) return;
  editingMachineId = machineId;
  resetProbe();
  document.querySelector('#area-select').value = String(machine.area_id);
  document.querySelector('#area-select').disabled = true;
  document.querySelector('#machine-name').value = machine.name;
  document.querySelector('#machine-ip').value = machine.ip;
  document.querySelector('#machine-db').value = machine.db_number;
  document.querySelector('#machine-timezone').value = machine.timezone;
  document.querySelector('#machine-form-eyebrow').textContent = 'EDITAR MÁQUINA';
  document.querySelector('#machine-form-title').textContent = 'Atualizar máquina';
  document.querySelector('#machine-submit-label').textContent = 'Salvar alterações';
  document.querySelector('#machine-cancel').hidden = false;
  machineForm.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function cancelEdit() {
  editingMachineId = null;
  machineForm.reset();
  resetProbe();
  document.querySelector('#area-select').disabled = false;
  document.querySelector('#machine-timezone').value = 'America/Sao_Paulo';
  document.querySelector('#machine-form-eyebrow').textContent = 'NOVA MÁQUINA';
  document.querySelector('#machine-form-title').textContent = 'Adicionar máquina';
  document.querySelector('#machine-submit-label').textContent = 'Salvar máquina';
  document.querySelector('#machine-cancel').hidden = true;
}

async function deleteMachine(machineId) {
  const machine = cachedMachines.find(function (item) { return item.id === machineId; });
  if (!machine || !confirm('Excluir a máquina "' + machine.name + '" e seu histórico?')) return;
  const response = await request('/api/config/machines/' + machineId, {method: 'DELETE'});
  document.querySelector('#machine-message').textContent = response.ok ? 'Máquina excluída.' : (await response.json()).detail;
  if (response.ok) { cancelEdit(); await refresh(); loadSettings(); }
}

function openLogin() { setupStatus(); dialog.showModal(); }

document.querySelector('#menu-toggle').onclick = function () { setMenu(!menu.classList.contains('open')); };
document.querySelector('#menu-close').onclick = function () { setMenu(false); };
menuBackdrop.onclick = function () { setMenu(false); };
document.querySelectorAll('.nav-button').forEach(function (button) {
  button.onclick = function () { navigate(button.dataset.route, {settings: true}); };
});
document.querySelector('#login-open-button').onclick = openLogin;
document.querySelector('#machine-cancel').onclick = cancelEdit;
document.querySelector('#machine-probe').onclick = scanMachine;
// The result belongs to the address that was swept, so editing the address clears
// it instead of leaving a list of DB numbers from another PLC on screen.
document.querySelector('#machine-ip').oninput = resetProbe;
document.querySelector('#machine-db').oninput = highlightDatabase;
document.querySelector('#setup-button').onclick = async function () {
  const password = document.querySelector('#setup-password').value;
  const response = await request('/api/setup/password', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: password})});
  message.textContent = response.ok ? 'Senha criada. Faça login para continuar.' : (await response.json()).detail;
  if (response.ok) setupStatus();
};
document.querySelector('#login-button').onclick = async function () {
  const password = document.querySelector('#login-password').value;
  const response = await request('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: password})});
  if (response.ok) {
    token = (await response.json()).token;
    sessionState.textContent = 'Logado - configuração autorizada';
    sessionState.classList.add('authenticated');
    dialog.close();
    if (!settingsPage.hidden) loadSettings();
  } else {
    message.textContent = 'Senha inválida.';
  }
};
document.querySelector('#area-form').onsubmit = async function (event) {
  event.preventDefault();
  const payload = {
    plant_name: document.querySelector('#plant-name').value,
    name: document.querySelector('#area-name').value,
  };
  const url = editingAreaId ? '/api/config/areas/' + editingAreaId : '/api/config/areas';
  const response = await request(url, {
    method: editingAreaId ? 'PUT' : 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const target = document.querySelector('#area-message');
  if (response.ok) {
    target.textContent = editingAreaId ? 'Área atualizada.' : 'Área salva.';
    cancelAreaEdit();
    await refresh();
    loadSettings();
  } else {
    const body = await response.json().catch(function () { return {}; });
    target.textContent = typeof body.detail === 'string' ? body.detail : 'Não foi possível salvar a área.';
  }
};
document.querySelector('#area-cancel').onclick = cancelAreaEdit;
document.querySelector('#plant-form').onsubmit = async function (event) {
  event.preventDefault();
  const slug = document.querySelector('#plant-select').value;
  const response = await request('/api/config/plants/' + encodeURIComponent(slug), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({plant_name: document.querySelector('#plant-rename').value}),
  });
  const target = document.querySelector('#plant-message');
  if (response.ok) {
    target.textContent = 'Planta renomeada.';
    document.querySelector('#plant-rename').value = '';
    await refresh();
    loadSettings();
  } else {
    const body = await response.json().catch(function () { return {}; });
    target.textContent = typeof body.detail === 'string' ? body.detail : 'Não foi possível renomear a planta.';
  }
};
machineForm.onsubmit = async function (event) {
  event.preventDefault();
  const payload = {
    name: document.querySelector('#machine-name').value,
    ip: document.querySelector('#machine-ip').value,
    db_number: Number(document.querySelector('#machine-db').value),
    timezone: document.querySelector('#machine-timezone').value
  };
  const url = editingMachineId ? '/api/config/machines/' + editingMachineId : '/api/config/areas/' + document.querySelector('#area-select').value + '/machines';
  const response = await request(url, {method: editingMachineId ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  document.querySelector('#machine-message').textContent = response.ok ? 'Máquina salva.' : (await response.json()).detail;
  if (response.ok) { cancelEdit(); await refresh(); loadSettings(); }
};

function labelKey(value) {
  // Mirrors fold_label() in app/plc.py: case and accents are not distinguishing.
  return String(value === null || value === undefined ? '' : value)
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .toLowerCase();
}

function labelInputs() {
  return Array.from(document.querySelectorAll('.signal-input'));
}

// Fixed signals keep their contract label, so a new name that matches one of them
// is a duplicate too.
function duplicateLabels() {
  const firstSeen = new Map();
  const repeated = new Map();
  function consider(value) {
    const key = labelKey(value);
    if (!key || repeated.has(key)) return;
    if (firstSeen.has(key)) { repeated.set(key, firstSeen.get(key)); return; }
    firstSeen.set(key, String(value).trim());
  }
  editorSignals.forEach(function (signal) {
    if (!signal.editable) consider(signal.label);
  });
  labelInputs().forEach(function (input) { consider(input.value); });
  return repeated;
}

function reviewLabels() {
  const repeated = duplicateLabels();
  labelInputs().forEach(function (input) {
    input.classList.toggle('duplicate', repeated.has(labelKey(input.value)));
  });
  const save = document.querySelector('#signals-save');
  save.disabled = repeated.size > 0;
  const target = document.querySelector('#signals-message');
  target.classList.toggle('warning', repeated.size > 0);
  if (repeated.size > 0) {
    target.textContent = 'Cada sinal precisa de um nome próprio. Repetido: ' + Array.from(repeated.values()).join(', ');
  } else {
    const locked = editorSignals.filter(function (signal) { return !signal.editable; }).length;
    target.textContent = `${locked} sinais fixos do contrato permanecem travados.`;
  }
  return repeated.size === 0;
}

function lockedSignalRow(signal) {
  return `<div class="signal-field locked"><span>${esc(signal.address)}<em>${esc(signal.type)}</em></span>`
    + `<span class="locked-label">${icon('lock')}${esc(signal.label)}</span></div>`;
}

function editableSignalRow(signal) {
  return `<label class="signal-field"><span>${esc(signal.address)}<em>${esc(signal.type)}</em></span>`
    + `<input class="signal-input" data-address="${esc(signal.address)}" maxlength="60" value="${esc(signal.label)}"></label>`;
}

async function openSignalLabels(machineId) {
  if (!token) {
    message.textContent = 'Faça login para alterar configurações.';
    dialog.showModal();
    return;
  }
  const response = await request('/api/config/machines/' + machineId + '/signals');
  if (!response.ok) return;
  const signals = await response.json();
  editingLabelsFor = machineId;
  editorSignals = signals;
  document.querySelector('#signals-editor').innerHTML = signals.map(function (signal) {
    return signal.editable ? editableSignalRow(signal) : lockedSignalRow(signal);
  }).join('');
  labelInputs().forEach(function (input) { input.oninput = reviewLabels; });
  reviewLabels();
  document.querySelector('#signals-dialog').showModal();
}

async function saveSignalLabels() {
  if (!reviewLabels()) return;
  const payload = labelInputs().map(function (input) {
    return { address: input.dataset.address, label: input.value };
  });
  const response = await request('/api/config/machines/' + editingLabelsFor + '/signals', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const target = document.querySelector('#signals-message');
  if (response.ok) {
    target.textContent = 'Rótulos salvos.';
    await refresh();
  } else {
    const body = await response.json().catch(function () { return {}; });
    target.textContent = typeof body.detail === 'string' ? body.detail : 'Não foi possível salvar os rótulos.';
  }
}

document.querySelector('#signals-save').onclick = saveSignalLabels;

function closeSignalLabels() {
  document.querySelector('#signals-dialog').close();
}

document.querySelector('#signals-close').onclick = closeSignalLabels;
document.querySelector('#signals-cancel').onclick = closeSignalLabels;

// Links rendered by this file carry data-route, so they stay inside the app.
window.addEventListener('popstate', function () { applyRoute(); });
document.addEventListener('click', function (event) {
  const link = event.target.closest ? event.target.closest('a[data-route]') : null;
  if (!link) return;
  event.preventDefault();
  navigate(link.dataset.route, {settings: true});
});

applyRoute({settings: true});
loadBranding();
loadAccess();
loadLibrary();
refresh();
setInterval(refresh, 5000);
