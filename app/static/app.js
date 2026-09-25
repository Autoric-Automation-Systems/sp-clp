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
let token = null;
let editingMachineId = null;
let cachedMachines = [];

async function request(url, options = {}) {
  const headers = {...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(url, {...options, headers});
}

function card(machine, status) {
  const signal = (name, label) => `<div class="status ${status[name] ? 'ok' : 'bad'}"><strong>${status[name] === null ? '—' : status[name] ? 'SIM' : 'NÃO'}</strong><small>${label}</small></div>`;
  return `<article class="machine-card"><h3>${machine.name}</h3><p class="meta">${machine.ip} · DB${machine.db_number} · ${status.connected ? 'Conectada' : 'Desconectada'}</p><div class="status-row">${signal('auto', 'Automático')}${signal('run', 'Em operação')}${signal('fault', 'Saudável')}</div><div class="count"><b>${status.count ?? '—'}</b><span>contador atual · ${status.timestamp ? new Date(status.timestamp).toLocaleTimeString() : 'sem leitura'}</span></div></article>`;
}

async function refresh() {
  const response = await request('/api/machines');
  cachedMachines = await response.json();
  empty.hidden = cachedMachines.length > 0;
  const cards = await Promise.all(cachedMachines.map(async machine => [machine, await (await request(`/api/machines/${machine.id}/status`)).json()]));
  machines.innerHTML = cards.map(([machine, status]) => card(machine, status)).join('');
  refreshState.textContent = `Atualizado às ${new Date().toLocaleTimeString()}`;
}

async function setupStatus() {
  const status = await (await request('/api/setup/status')).json();
  document.querySelector('#setup-fields').hidden = status.password_configured;
  document.querySelector('#login-fields').hidden = !status.password_configured;
}

function showPage(page) {
  const settings = page === 'settings';
  dashboardPage.hidden = settings;
  settingsPage.hidden = !settings;
  document.querySelectorAll('.nav-button').forEach(button => button.classList.toggle('active', button.dataset.page === page));
  if (settings) loadSettings();
}

async function loadSettings() {
  if (!token) return;
  settingsLocked.hidden = true;
  settingsContent.hidden = false;
  const response = await request('/api/config/areas');
  if (!response.ok) return;
  const areas = await response.json();
  document.querySelector('#area-select').innerHTML = areas.length ? areas.map(area => `<option value="${area.id}">${area.plant_name} / ${area.name}</option>`).join('') : '<option value="">Crie uma área primeiro</option>';
  const machinesByArea = cachedMachines.reduce((groups, machine) => { (groups[machine.area_id] ||= []).push(machine); return groups; }, {});
  document.querySelector('#area-list').innerHTML = areas.length ? areas.map(area => `<div class="area-item"><div class="list-item"><strong>${area.name}</strong><span>${area.plant_name}</span></div>${(machinesByArea[area.id] || []).map(machine => `<div class="machine-list-item"><span>${machine.name} · ${machine.ip} · DB${machine.db_number}</span><span class="machine-actions"><button type="button" class="edit-machine" data-machine-id="${machine.id}">Editar</button><button type="button" class="delete-machine danger" data-machine-id="${machine.id}">Excluir</button></span></div>`).join('') || '<p class="form-hint">Nenhuma máquina nesta área.</p>'}</div>`).join('') : '<p class="form-hint">Nenhuma área cadastrada.</p>';
  document.querySelectorAll('.edit-machine').forEach(button => button.onclick = () => beginEditMachine(Number(button.dataset.machineId)));
  document.querySelectorAll('.delete-machine').forEach(button => button.onclick = () => deleteMachine(Number(button.dataset.machineId)));
}

async function deleteMachine(machineId) {
  const machine = cachedMachines.find(item => item.id === machineId);
  if (!machine || !confirm(`Excluir a máquina "${machine.name}" e seu histórico?`)) return;
  const response = await request(`/api/config/machines/${machineId}`, {method: 'DELETE'});
  document.querySelector('#machine-message').textContent = response.ok ? 'Máquina excluída.' : (await response.json()).detail;
  if (response.ok) { cancelEditMachine(); await refresh(); loadSettings(); }
}

function beginEditMachine(machineId) {
  const machine = cachedMachines.find(item => item.id === machineId);
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
  document.querySelector('#machine-submit').textContent = 'Salvar alterações';
  document.querySelector('#machine-cancel').hidden = false;
  machineForm.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function cancelEditMachine() {
  editingMachineId = null;
  machineForm.reset();
  document.querySelector('#area-select').disabled = false;
  document.querySelector('#machine-timezone').value = 'America/Sao_Paulo';
  document.querySelector('#machine-form-eyebrow').textContent = 'NOVA MÁQUINA';
  document.querySelector('#machine-form-title').textContent = 'Adicionar máquina';
  document.querySelector('#machine-submit').textContent = 'Salvar máquina';
  document.querySelector('#machine-cancel').hidden = true;
}

function openLogin() { setupStatus(); dialog.showModal(); }
document.querySelectorAll('.nav-button').forEach(button => button.onclick = () => showPage(button.dataset.page));
document.querySelector('#login-open-button').onclick = openLogin;
document.querySelector('#machine-cancel').onclick = cancelEditMachine;
document.querySelector('#setup-button').onclick = async () => {
  const password = document.querySelector('#setup-password').value;
  const response = await request('/api/setup/password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password})});
  message.textContent = response.ok ? 'Senha criada. Faça login para continuar.' : (await response.json()).detail;
  if (response.ok) setupStatus();
};
document.querySelector('#login-button').onclick = async () => {
  const password = document.querySelector('#login-password').value;
  const response = await request('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password})});
  if (response.ok) { token = (await response.json()).token; sessionState.textContent = 'Logado · Configuração autorizada'; sessionState.classList.add('authenticated'); message.textContent = 'Login confirmado. Configurações liberadas.'; dialog.close(); if (!settingsPage.hidden) loadSettings(); } else message.textContent = 'Senha inválida.';
};
document.querySelector('#area-form').onsubmit = async event => { event.preventDefault(); const response = await request('/api/config/areas', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({plant_name:document.querySelector('#plant-name').value, name:document.querySelector('#area-name').value})}); document.querySelector('#area-message').textContent = response.ok ? 'Área salva.' : (await response.json()).detail; if (response.ok) { event.target.reset(); loadSettings(); } };
machineForm.onsubmit = async event => { event.preventDefault(); const payload = {name:document.querySelector('#machine-name').value, ip:document.querySelector('#machine-ip').value, db_number:Number(document.querySelector('#machine-db').value), timezone:document.querySelector('#machine-timezone').value}; const url = editingMachineId ? `/api/config/machines/${editingMachineId}` : `/api/config/areas/${document.querySelector('#area-select').value}/machines`; const response = await request(url, {method: editingMachineId ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); document.querySelector('#machine-message').textContent = response.ok ? (editingMachineId ? 'Máquina atualizada.' : 'Máquina salva.') : (await response.json()).detail; if (response.ok) { cancelEditMachine(); await refresh(); loadSettings(); } };
refresh();
setInterval(refresh, 5000);
