const machines = document.querySelector('#machines');
const empty = document.querySelector('#empty');
const refreshState = document.querySelector('#refresh-state');
const dialog = document.querySelector('#settings-dialog');
const message = document.querySelector('#settings-message');
const sessionState = document.querySelector('#session-state');
const dashboardPage = document.querySelector('#dashboard-page');
const settingsPage = document.querySelector('#settings-page');
const settingsLocked = document.querySelector('#settings-locked');
const settingsContent = document.querySelector('#settings-content');
const machineForm = document.querySelector('#machine-form');
const menu = document.querySelector('#main-menu');
const menuBackdrop = document.querySelector('#menu-backdrop');
let token = null;
let editingMachineId = null;
let cachedMachines = [];

async function request(url, options = {}) {
  const headers = {...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(url, {...options, headers});
}

function statusBlock(value, label, icon) {
  const state = value === null ? 'unknown' : value ? 'ok' : 'bad';
  const text = value === null ? 'Sem leitura' : value ? 'Ativo' : 'Parado';
  return `<div class="state ${state}"><span class="state-icon">${icon}</span><div><strong>${text}</strong><small>${label}</small></div></div>`;
}

function machineCard(machine, status) {
  return `<article class="machine-card"><div class="machine-card-head"><div><span class="machine-kicker">DB${machine.db_number}</span><h3>${machine.name}</h3><p class="meta">${machine.ip}</p></div><span class="connection-pill ${status.connected ? 'online' : 'offline'}"><i></i>${status.connected ? 'Online' : 'Offline'}</span></div><div class="state-grid">${statusBlock(status.auto, 'Automatico', 'A')}${statusBlock(status.run, 'Producao', '>')}${statusBlock(status.fault, 'Saude', '+')}</div><div class="count"><span class="count-label">CONTADOR ATUAL</span><b>${status.count ?? '-'}</b><span>${status.timestamp ? 'Atualizado as ' + new Date(status.timestamp).toLocaleTimeString() : 'Aguardando leitura do CLP'}</span></div></article>`;
}

function renderGroups(items) {
  const plants = {};
  items.forEach(function (entry) {
    const machine = entry[0];
    const status = entry[1];
    const plant = machine.plant_name || 'Planta sem nome';
    const area = machine.area_name || 'Area sem nome';
    if (!plants[plant]) plants[plant] = {};
    if (!plants[plant][area]) plants[plant][area] = [];
    plants[plant][area].push(machineCard(machine, status));
  });
  return Object.keys(plants).map(function (plant) {
    const areas = plants[plant];
    const areaHtml = Object.keys(areas).map(function (area) {
      const cards = areas[area];
      const label = cards.length === 1 ? '1 maquina' : cards.length + ' maquinas';
      return `<div class="area-group"><div class="area-heading"><span class="area-mark">/</span><h4>${area}</h4><span>${label}</span></div><div class="machine-grid">${cards.join('')}</div></div>`;
    }).join('');
    return `<section class="plant-group"><div class="plant-heading"><span class="section-index">PLANTA</span><h3>${plant}</h3></div>${areaHtml}</section>`;
  }).join('');
}

async function refresh() {
  const response = await request('/api/machines');
  cachedMachines = await response.json();
  empty.hidden = cachedMachines.length > 0;
  const items = await Promise.all(cachedMachines.map(async function (machine) {
    const statusResponse = await request('/api/machines/' + machine.id + '/status');
    return [machine, await statusResponse.json()];
  }));
  machines.innerHTML = renderGroups(items);
  refreshState.textContent = 'Atualizado as ' + new Date().toLocaleTimeString();
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

function showPage(page) {
  const isSettings = page === 'settings';
  dashboardPage.hidden = isSettings;
  settingsPage.hidden = !isSettings;
  document.querySelectorAll('.nav-button').forEach(function (button) {
    button.classList.toggle('active', button.dataset.page === page);
  });
  setMenu(false);
  if (isSettings) loadSettings();
}

async function loadSettings() {
  if (!token) return;
  settingsLocked.hidden = true;
  settingsContent.hidden = false;
  const response = await request('/api/config/areas');
  if (!response.ok) return;
  const areas = await response.json();
  document.querySelector('#area-select').innerHTML = areas.length
    ? areas.map(function (area) { return `<option value="${area.id}">${area.plant_name} / ${area.name}</option>`; }).join('')
    : '<option value="">Crie uma area primeiro</option>';
  const byArea = cachedMachines.reduce(function (groups, machine) {
    if (!groups[machine.area_id]) groups[machine.area_id] = [];
    groups[machine.area_id].push(machine);
    return groups;
  }, {});
  document.querySelector('#area-list').innerHTML = areas.length
    ? areas.map(function (area) {
        const list = (byArea[area.id] || []).map(function (machine) {
          return `<div class="machine-list-item"><span>${machine.name} - ${machine.ip} - DB${machine.db_number}</span><span class="machine-actions"><button type="button" class="edit-machine" data-machine-id="${machine.id}">Editar</button><button type="button" class="delete-machine danger" data-machine-id="${machine.id}">Excluir</button></span></div>`;
        }).join('') || '<p class="form-hint">Nenhuma maquina nesta area.</p>';
        return `<div class="area-item"><div class="list-item"><strong>${area.name}</strong><span>${area.plant_name}</span></div>${list}</div>`;
      }).join('')
    : '<p class="form-hint">Nenhuma area cadastrada.</p>';
  document.querySelectorAll('.edit-machine').forEach(function (button) {
    button.onclick = function () { beginEdit(Number(button.dataset.machineId)); };
  });
  document.querySelectorAll('.delete-machine').forEach(function (button) {
    button.onclick = function () { deleteMachine(Number(button.dataset.machineId)); };
  });
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
  document.querySelector('#machine-form-eyebrow').textContent = 'EDITAR MAQUINA';
  document.querySelector('#machine-form-title').textContent = 'Atualizar maquina';
  document.querySelector('#machine-submit').textContent = 'Salvar alteracoes';
  document.querySelector('#machine-cancel').hidden = false;
  machineForm.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function cancelEdit() {
  editingMachineId = null;
  machineForm.reset();
  document.querySelector('#area-select').disabled = false;
  document.querySelector('#machine-timezone').value = 'America/Sao_Paulo';
  document.querySelector('#machine-form-eyebrow').textContent = 'NOVA MAQUINA';
  document.querySelector('#machine-form-title').textContent = 'Adicionar maquina';
  document.querySelector('#machine-submit').textContent = 'Salvar maquina';
  document.querySelector('#machine-cancel').hidden = true;
}

async function deleteMachine(machineId) {
  const machine = cachedMachines.find(function (item) { return item.id === machineId; });
  if (!machine || !confirm('Excluir a maquina "' + machine.name + '" e seu historico?')) return;
  const response = await request('/api/config/machines/' + machineId, {method: 'DELETE'});
  document.querySelector('#machine-message').textContent = response.ok ? 'Maquina excluida.' : (await response.json()).detail;
  if (response.ok) { cancelEdit(); await refresh(); loadSettings(); }
}

function openLogin() { setupStatus(); dialog.showModal(); }

document.querySelector('#menu-toggle').onclick = function () { setMenu(!menu.classList.contains('open')); };
document.querySelector('#menu-close').onclick = function () { setMenu(false); };
menuBackdrop.onclick = function () { setMenu(false); };
document.querySelectorAll('.nav-button').forEach(function (button) {
  button.onclick = function () { showPage(button.dataset.page); };
});
document.querySelector('#login-open-button').onclick = openLogin;
document.querySelector('#machine-cancel').onclick = cancelEdit;
document.querySelector('#setup-button').onclick = async function () {
  const password = document.querySelector('#setup-password').value;
  const response = await request('/api/setup/password', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: password})});
  message.textContent = response.ok ? 'Senha criada. Faca login para continuar.' : (await response.json()).detail;
  if (response.ok) setupStatus();
};
document.querySelector('#login-button').onclick = async function () {
  const password = document.querySelector('#login-password').value;
  const response = await request('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: password})});
  if (response.ok) {
    token = (await response.json()).token;
    sessionState.textContent = 'Logado - Configuracao autorizada';
    sessionState.classList.add('authenticated');
    dialog.close();
    if (!settingsPage.hidden) loadSettings();
  } else {
    message.textContent = 'Senha invalida.';
  }
};
document.querySelector('#area-form').onsubmit = async function (event) {
  event.preventDefault();
  const response = await request('/api/config/areas', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({plant_name: document.querySelector('#plant-name').value, name: document.querySelector('#area-name').value})});
  document.querySelector('#area-message').textContent = response.ok ? 'Area salva.' : (await response.json()).detail;
  if (response.ok) { event.target.reset(); loadSettings(); }
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
  document.querySelector('#machine-message').textContent = response.ok ? 'Maquina salva.' : (await response.json()).detail;
  if (response.ok) { cancelEdit(); await refresh(); loadSettings(); }
};

refresh();
setInterval(refresh, 5000);
