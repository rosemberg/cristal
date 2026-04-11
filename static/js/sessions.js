/* Cristal 2.0 — Sessions: gerenciamento de sessões na sidebar (Etapa 14) */
/* Depende de: api.js (window.API) */
(function (root) {
  'use strict';

  var STORAGE_KEY = 'cristal_session_id';

  // Estado
  var _currentSessionId = null;

  // ===== Persistência local =====
  function saveCurrentSession(id) {
    _currentSessionId = id;
    try { localStorage.setItem(STORAGE_KEY, id || ''); } catch (_) {}
  }

  function loadStoredSession() {
    try { return localStorage.getItem(STORAGE_KEY) || null; } catch (_) { return null; }
  }

  // ===== API =====
  async function createSession(title) {
    try {
      var res = await API.createSession(title || null);
      if (!res.ok) return null;
      var data = await res.json();
      saveCurrentSession(data.id);
      return data;
    } catch (_) { return null; }
  }

  async function ensureSession() {
    if (_currentSessionId) return _currentSessionId;
    var session = await createSession();
    return session ? session.id : null;
  }

  // ===== Carregar histórico de uma sessão =====
  async function loadSessionHistory(sessionId) {
    try {
      var res = await API.getSessionMessages(sessionId);
      if (!res.ok) return [];
      var data = await res.json();
      return data.messages || [];
    } catch (_) { return []; }
  }

  // ===== Renderizar lista de sessões no sidebar =====
  async function renderSessionsList() {
    var list = document.getElementById('sessions-list');
    if (!list) return;

    try {
      var res = await API.listSessions();
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();
      var sessions = data.sessions || [];

      if (sessions.length === 0) {
        list.innerHTML = '<p class="sidebar-empty-state">Sem conversas anteriores.<br>Comece digitando sua pergunta.</p>';
        return;
      }

      list.innerHTML = '';
      sessions.forEach(function (s) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'session-item' + (s.id === _currentSessionId ? ' session-item--active' : '');
        item.setAttribute('aria-label', 'Carregar conversa: ' + (s.title || 'Conversa'));
        item.setAttribute('data-session-id', s.id);

        var titleEl = document.createElement('span');
        titleEl.className = 'session-item-title';
        titleEl.textContent = s.title || 'Conversa sem título';
        item.appendChild(titleEl);

        var dateEl = document.createElement('span');
        dateEl.className = 'session-item-date';
        dateEl.textContent = formatRelativeDate(s.last_active);
        item.appendChild(dateEl);

        item.addEventListener('click', function () { onSessionClick(s); });
        list.appendChild(item);
      });
    } catch (_) {
      list.innerHTML = '<p class="sidebar-empty-state">Erro ao carregar conversas.</p>';
    }
  }

  // ===== Ao clicar numa sessão — carrega no chat =====
  async function onSessionClick(session) {
    saveCurrentSession(session.id);

    // Fecha sidebar no mobile
    if (root.App && root.App.closeSidebar) root.App.closeSidebar();

    // Marca como ativa na lista
    document.querySelectorAll('.session-item').forEach(function (el) {
      el.classList.toggle('session-item--active', el.getAttribute('data-session-id') === session.id);
    });

    // Carrega histórico e renderiza no chat
    var messages = await loadSessionHistory(session.id);
    if (root.Chat && root.Chat.loadHistory) {
      root.Chat.loadHistory(messages, session.id);
    }
  }

  // ===== Formatação de data relativa =====
  function formatRelativeDate(isoString) {
    if (!isoString) return '';
    try {
      var d = new Date(isoString);
      var now = new Date();
      var diffMs = now - d;
      var diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1)  return 'agora';
      if (diffMin < 60) return diffMin + 'min atrás';
      var diffH = Math.floor(diffMin / 60);
      if (diffH < 24)   return diffH + 'h atrás';
      var diffD = Math.floor(diffH / 24);
      if (diffD === 1)  return 'ontem';
      if (diffD < 7)    return diffD + 'd atrás';
      return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
    } catch (_) { return ''; }
  }

  // ===== Init =====
  function init() {
    _currentSessionId = loadStoredSession();
    renderSessionsList();
  }

  // ===== API Pública =====
  root.Sessions = {
    init:              init,
    ensureSession:     ensureSession,
    createSession:     createSession,
    getCurrentId:      function () { return _currentSessionId; },
    saveCurrentSession: saveCurrentSession,
    refreshList:       renderSessionsList,
  };

})(window);
