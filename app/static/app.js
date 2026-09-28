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
// Hourly totals only change once per hour, so they are fetched on demand instead of
// riding the 5 s status poll. Every refresh rebuilds the cards, so the open state and
// the fetched rows are kept outside the markup.
const hourlyRows = new Map();
const hourlyOpen = new Set();
const signalsOpen = new Set();
const HOURLY_VISIBLE = 8;

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

function stateOf(item) {
  // A null value means the PLC could not be read, which is not the same as a false bit.
  if (!item || item.value === null || item.value === undefined) return 'unknown';
  return item.value ? 'ok' : 'bad';
}

function stateIcon(state) {
  if (state === 'ok') return 'circle-check';
  if (state === 'bad') return 'circle-x';
  return 'circle-help';
}

function signalIcon(item) {
  if (item.kind === 'fault') return 'triangle-alert';
  if (item.kind === 'safety') return 'shield-check';
  if (item.kind === 'auto') return 'power';
  if (item.kind === 'run') return 'play';
  if (item.kind === 'counter') return 'gauge';
  return 'tag';
}

function statusBlock(item) {
  if (!item) return '';
  const state = stateOf(item);
  const text = state === 'unknown' ? 'Sem leitura' : item.value ? 'Ativo' : 'Parado';
  return `<div class="state ${state}"><span class="state-icon">${icon(signalIcon(item))}</span><div><strong>${text}</strong><small>${esc(item.label)}</small></div></div>`;
}

function signalsOfKind(status, kind) {
  return (status.signals || []).filter(function (item) { return item.kind === kind; });
}

function firstOfKind(status, kind) {
  return signalsOfKind(status, kind)[0] || null;
}

function hourLabel(localHour, newestDate) {
  const date = localHour.slice(0, 10);
  const time = localHour.slice(11, 16);
  return date === newestDate ? time : `${date.slice(8, 10)}/${date.slice(5, 7)} ${time}`;
}

function hourlySection(machine) {
  const open = hourlyOpen.has(machine.id);
  const rows = hourlyRows.get(machine.id);
  let body = '';
  if (open) {
    if (rows === 'loading') {
      body = '<p class="hourly-note">Carregando...</p>';
    } else if (!rows || rows.length === 0) {
      body = '<p class="hourly-note">Sem historico horario registrado.</p>';
    } else {
      const newestDate = rows[0].local_hour.slice(0, 10);
      const items = rows.slice(0, HOURLY_VISIBLE).map(function (row) {
        return `<li><span>${hourLabel(row.local_hour, newestDate)}</span><b>${row.quantity}</b></li>`;
      }).join('');
      body = `<ol class="hourly-list">${items}</ol>`;
    }
  }
  const action = open ? 'Ocultar' : 'Ver';
  return `<div class="hourly"><button type="button" class="hourly-toggle" data-hourly-id="${machine.id}" aria-expanded="${open}"><span class="toggle-label">${icon('clock')}${action} contagens por hora</span>${icon(open ? 'chevron-up' : 'chevron-down')}</button>${body}</div>`;
}

function signalsSection(machine, status) {
  const custom = signalsOfKind(status, 'custom');
  const open = signalsOpen.has(machine.id);
  let body = '';
  if (open) {
    body = '<ul class="signal-list">' + custom.map(function (item) {
      const state = stateOf(item);
      const text = state === 'unknown' ? '-' : item.value ? 'Ativo' : 'Parado';
      return `<li class="signal-row"><span class="signal-address">${esc(item.address)}</span><span class="signal-name">${esc(item.label)}</span><span class="signal-state ${state}">${icon(stateIcon(state))}${text}</span></li>`;
    }).join('') + '</ul>';
  }
  const action = open ? 'Ocultar' : 'Ver';
  return `<div class="signals"><button type="button" class="signals-toggle" data-signals-id="${machine.id}" aria-expanded="${open}"><span class="toggle-label">${icon('list')}${action} sinais (${custom.length})</span>${icon(open ? 'chevron-up' : 'chevron-down')}</button>${body}</div>`;
}

function machineCard(machine, status) {
  const counter = firstOfKind(status, 'counter');
  const counterLabel = counter ? `CONTADOR ${esc(counter.label)}` : 'CONTADOR ATUAL';
  const connection = status.connected ? 'online' : 'offline';
  return `<article class="machine-card"><div class="machine-card-head"><div><span class="machine-kicker">${icon('cpu')}DB${esc(machine.db_number)}</span><h3>${esc(machine.name)}</h3><p class="meta">${esc(machine.ip)}</p></div><span class="connection-pill ${connection}">${icon(connection === 'online' ? 'wifi' : 'wifi-off')}${status.connected ? 'Online' : 'Offline'}</span></div><div class="state-grid">${statusBlock(firstOfKind(status, 'auto'))}${statusBlock(firstOfKind(status, 'run'))}${statusBlock(firstOfKind(status, 'fault'))}${statusBlock(firstOfKind(status, 'safety'))}</div><div class="count"><span class="count-label">${icon('gauge')}${counterLabel}</span><b>${status.count ?? '-'}</b><span>${status.timestamp ? 'Atualizado às ' + new Date(status.timestamp).toLocaleTimeString() : 'Aguardando leitura do CLP'}</span></div>${signalsSection(machine, status)}${hourlySection(machine)}</article>`;
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
  document.querySelectorAll('.hourly-toggle').forEach(function (button) {
    button.onclick = function () { toggleHourly(Number(button.dataset.hourlyId)); };
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

async function loadHourly(machineId) {
  const cached = hourlyRows.get(machineId);
  if (cached && cached !== 'loading') return;
  hourlyRows.set(machineId, 'loading');
  renderCards();
  const response = await request('/api/machines/' + machineId + '/hourly-counts');
  hourlyRows.set(machineId, response.ok ? await response.json() : []);
  renderCards();
}

function toggleHourly(machineId) {
  if (hourlyOpen.has(machineId)) {
    hourlyOpen.delete(machineId);
    renderCards();
    return;
  }
  hourlyOpen.add(machineId);
  renderCards();
  loadHourly(machineId);
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

async function loadBranding() {
  const response = await request('/api/branding');
  if (response.ok) applyBranding(await response.json());
}

function fileToBase64(file) {
  return new Promise(function (resolve, reject) {
    const reader = new FileReader();
    reader.onload = function () {
      // "data:image/png;base64,AAAA" -> "AAAA"
      resolve(String(reader.result).split(',')[1] || '');
    };
    reader.onerror = function () { reject(new Error('read failed')); };
    reader.readAsDataURL(file);
  });
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

document.querySelector('#branding-form').onsubmit = async function (event) {
  event.preventDefault();
  const name = await request('/api/config/branding', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({company_name: document.querySelector('#company-name').value}),
  });
  if (!(await showBrandingMessage(name, 'Identidade salva.'))) return;

  const file = document.querySelector('#company-logo').files[0];
  if (!file) return;
  let content = '';
  try {
    content = await fileToBase64(file);
  } catch (error) {
    document.querySelector('#branding-message').textContent = 'Não foi possível ler o arquivo.';
    return;
  }
  const logo = await request('/api/config/branding/logo', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({filename: file.name, content: content}),
  });
  await showBrandingMessage(logo, 'Identidade e logotipo salvos.');
  if (logo.ok) document.querySelector('#company-logo').value = '';
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

function beginEdit(machineId) {
  const machine = cachedMachines.find(function (item) { return item.id === machineId; });
  if (!machine) return;
  editingMachineId = machineId;
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
refresh();
setInterval(refresh, 5000);
