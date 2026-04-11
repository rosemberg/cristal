/* Cristal 2.0 — App Shell (Etapa 14) */
/* Gerencia: sidebar, docs panel, categorias, navegação chat/mapa, sessões */
(function () {
  'use strict';

  // ===== Referências DOM =====
  var sidebar        = document.getElementById('sidebar');
  var sidebarToggle  = document.getElementById('sidebar-toggle');
  var sidebarClose   = document.getElementById('sidebar-close');
  var sidebarOverlay = document.getElementById('sidebar-overlay');
  var docsPanel      = document.getElementById('docs-panel');
  var docsPanelClose = document.getElementById('docs-panel-close');
  var docsPanelTitle = document.getElementById('docs-panel-title');
  var docsPanelBody  = document.getElementById('docs-panel-body');
  var newChatBtn     = document.getElementById('new-chat-btn');
  var categoriesList = document.getElementById('categories-list');
  var mapBtn         = document.getElementById('map-btn');

  // ===== Estado de navegação =====
  var _currentView = 'chat'; // 'chat' | 'map'

  // ===== Sidebar =====

  function isMobile() {
    return window.innerWidth < 1024;
  }

  function openSidebar() {
    if (!sidebar) return;
    sidebar.classList.add('open');
    if (sidebarOverlay) sidebarOverlay.classList.add('visible');
    if (sidebarToggle) sidebarToggle.setAttribute('aria-expanded', 'true');
    if (isMobile()) document.body.style.overflow = 'hidden';
  }

  function closeSidebar() {
    if (!sidebar) return;
    sidebar.classList.remove('open');
    if (sidebarOverlay) sidebarOverlay.classList.remove('visible');
    if (sidebarToggle) sidebarToggle.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
  }

  if (sidebarToggle) {
    sidebarToggle.addEventListener('click', function () {
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });
  }

  if (sidebarClose)   sidebarClose.addEventListener('click', closeSidebar);
  if (sidebarOverlay) sidebarOverlay.addEventListener('click', closeSidebar);

  window.addEventListener('resize', function () {
    if (!isMobile()) closeSidebar();
  });

  // ===== Navegação de views =====

  function navigate(view) {
    _currentView = view;

    var chatArea   = document.getElementById('messages-area');
    var inputArea  = document.querySelector('.input-area-wrapper');
    var chatTopbar = document.querySelector('.chat-topbar');
    var mapView    = document.getElementById('map-view');

    if (view === 'map') {
      if (chatArea)   chatArea.style.display   = 'none';
      if (inputArea)  inputArea.style.display  = 'none';
      if (chatTopbar) chatTopbar.style.display = 'none';
      if (mapView)    { mapView.style.display = ''; mapView.removeAttribute('hidden'); }
      if (mapBtn)     mapBtn.classList.add('nav-btn--active');
      // Carrega o mapa se ainda não carregado
      if (window.Map) window.Map.load();
    } else {
      if (chatArea)   chatArea.style.display   = '';
      if (inputArea)  inputArea.style.display  = '';
      if (chatTopbar) chatTopbar.style.display = '';
      if (mapView)    mapView.setAttribute('hidden', '');
      if (mapBtn)     mapBtn.classList.remove('nav-btn--active');
    }
  }

  if (mapBtn) {
    mapBtn.addEventListener('click', function () {
      navigate(_currentView === 'map' ? 'chat' : 'map');
    });
  }

  // ===== Docs Panel =====

  function openDocsPanel(doc) {
    if (!docsPanel) return;
    if (docsPanelTitle) {
      docsPanelTitle.textContent = (doc && doc.title) ? doc.title : 'Documento';
    }
    if (docsPanelBody && doc) {
      if (doc.url && window.Documents) {
        window.Documents.openMeta(doc.url, doc);
      } else if (doc.html) {
        docsPanelBody.innerHTML = doc.html;
      }
    }
    docsPanel.removeAttribute('hidden');
  }

  function closeDocsPanel() {
    if (docsPanel) docsPanel.setAttribute('hidden', '');
  }

  if (docsPanelClose) {
    docsPanelClose.addEventListener('click', closeDocsPanel);
  }

  // ===== Nova Conversa =====

  if (newChatBtn) {
    newChatBtn.addEventListener('click', async function () {
      closeSidebar();
      navigate('chat');

      var messagesArea = document.getElementById('messages-area');
      if (messagesArea) {
        Array.from(messagesArea.children).forEach(function (child) {
          if (child.id !== 'welcome-message') child.remove();
        });
      }

      // Cria nova sessão
      if (window.Sessions) {
        var session = await window.Sessions.createSession();
        if (session) window.Sessions.refreshList();
      }

      if (window._chatState) {
        window._chatState.history   = [];
        window._chatState.isLoading = false;
        window._chatState.sessionId = window.Sessions ? window.Sessions.getCurrentId() : null;
      }

      var inputField = document.getElementById('input-field');
      if (inputField) {
        inputField.value = '';
        inputField.dispatchEvent(new Event('input'));
        inputField.focus();
      }
    });
  }

  // ===== Categorias =====

  async function loadCategories() {
    if (!categoriesList) return;

    try {
      var res = await API.categories();
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();
      var cats = data.categories || [];

      if (cats.length === 0) {
        categoriesList.innerHTML =
          '<li><p class="sidebar-empty-state">Nenhuma categoria disponível.</p></li>';
        return;
      }

      categoriesList.innerHTML = '';

      cats.forEach(function (cat) {
        var name = cat.name || cat.category || '';
        if (!name) return;

        var li = document.createElement('li');
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'category-item';
        btn.setAttribute('aria-label', 'Consultar categoria: ' + name);

        var nameSpan = document.createElement('span');
        nameSpan.className = 'category-item-name';
        nameSpan.textContent = name;
        btn.appendChild(nameSpan);

        var count = cat.page_count || cat.count || 0;
        if (count) {
          var countSpan = document.createElement('span');
          countSpan.className = 'category-item-count';
          countSpan.textContent = count;
          btn.appendChild(countSpan);
        }

        btn.addEventListener('click', function () {
          closeSidebar();
          navigate('chat');
          var inputField = document.getElementById('input-field');
          if (inputField) {
            inputField.value = 'O que posso encontrar em ' + name + '?';
            inputField.dispatchEvent(new Event('input'));
            inputField.focus();
          }
        });

        li.appendChild(btn);
        categoriesList.appendChild(li);
      });
    } catch (_err) {
      categoriesList.innerHTML =
        '<li><p class="sidebar-empty-state">Erro ao carregar categorias.</p></li>';
    }
  }

  // ===== Tecla Escape =====

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (sidebar && sidebar.classList.contains('open')) {
      closeSidebar();
    } else if (docsPanel && !docsPanel.hasAttribute('hidden')) {
      closeDocsPanel();
    } else if (_currentView === 'map') {
      navigate('chat');
    }
  });

  // ===== API Pública =====
  window.App = {
    openDocsPanel:  openDocsPanel,
    closeDocsPanel: closeDocsPanel,
    openSidebar:    openSidebar,
    closeSidebar:   closeSidebar,
    navigate:       navigate,
  };

  // ===== Init =====
  loadCategories();
  if (window.Sessions) window.Sessions.init();

})();
