/* Cristal 2.0 — Chat: lógica do chat (Etapa 13) */
/* Depende de: utils.js (window.Utils) e api.js (window.API) */
(function () {
  'use strict';

  // ===== SVG Icons =====
  var ICONS = {
    scale: '<svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3"/></svg>',
    user: '<svg width="20" height="20" class="icon-user" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>',
    fileText: '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
    link: '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>',
    externalLink: '<svg width="14" height="14" class="icon-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>',
    search: '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>',
    chevronDown: '<svg width="16" height="16" class="accordion-arrow" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>',
    alertCircle: '<svg width="16" height="16" class="icon-inline" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    download: '<svg width="16" height="16" class="icon-inline icon-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
    play: '<svg width="16" height="16" class="icon-inline icon-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    code: '<svg width="16" height="16" class="icon-inline icon-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>',
    page: '<svg width="16" height="16" class="icon-inline icon-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
    thumbUp: '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/></svg>',
    thumbDown: '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018a2 2 0 01.485.06l3.76.94m-7 10v5a2 2 0 002 2h.096c.5 0 .905-.405.905-.904 0-.715.211-1.413.608-2.008L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"/></svg>',
  };

  var LINK_TYPE_ICON = {
    page: 'page', pdf: 'download', csv: 'download',
    video: 'play', audio: 'play', api: 'code', external: 'externalLink',
  };

  // ===== Estado =====
  var state = {
    history:   [],
    isLoading: false,
    sessionId: null,
  };

  // Expõe para app.js (botão Nova Conversa)
  window._chatState = state;

  // ===== DOM =====
  var messagesArea = document.getElementById('messages-area');
  var inputField   = document.getElementById('input-field');
  var sendButton   = document.getElementById('send-button');

  // ===== Init =====
  function init() {
    loadInitialSuggestions();
    inputField.addEventListener('keydown', handleKeydown);
    sendButton.addEventListener('click', handleSend);
    inputField.addEventListener('input', autoResize);

    document.querySelectorAll('#welcome-chips .suggestion-chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!state.isLoading) sendMessage(btn.textContent.trim());
      });
    });
  }

  async function loadInitialSuggestions() {
    try {
      var res = await API.suggest();
      if (!res.ok) return;
      var data = await res.json();
      var welcomeChips = document.getElementById('welcome-chips');
      if (welcomeChips && data.suggestions && data.suggestions.length) {
        welcomeChips.innerHTML = '';
        data.suggestions.forEach(function (s) { welcomeChips.appendChild(createChip(s)); });
      }
    } catch (_) { /* silencioso */ }
  }

  // ===== Handlers =====
  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
  }

  function handleSend() {
    var text = inputField.value.trim();
    if (!text || state.isLoading) return;
    sendMessage(text);
  }

  function autoResize() {
    inputField.style.height = 'auto';
    inputField.style.height = Math.min(inputField.scrollHeight, 120) + 'px';
  }

  // ===== Carregar histórico de sessão =====
  function loadHistory(messages, sessionId) {
    state.sessionId = sessionId || null;
    state.history   = [];

    // Limpa mensagens existentes (exceto boas-vindas)
    Array.from(messagesArea.children).forEach(function (child) {
      if (child.id !== 'welcome-message') child.remove();
    });

    // Renderiza mensagens
    messages.forEach(function (m) {
      if (m.role === 'user') {
        appendUserMessage(m.content);
        state.history.push({ role: 'user', content: m.content });
      } else if (m.role === 'assistant') {
        // Constrói data compatível com buildBotCard
        var data = {
          text:    m.content,
          links:   [],
          sources: m.sources || [],
          tables:  m.tables  || [],
        };
        appendBotMessage(data);
        state.history.push({ role: 'assistant', content: m.content });
      }
    });

    scrollToBottom();
  }

  // ===== Envio com contexto de documento =====
  function sendWithContext(docTitle) {
    var text = 'Fale mais sobre o documento: ' + docTitle;
    if (!state.isLoading) sendMessage(text);
  }

  // ===== Envio de mensagem =====
  async function sendMessage(text) {
    state.isLoading = true;
    sendButton.disabled = true;
    inputField.value = '';
    inputField.style.height = 'auto';

    // Garante sessão antes do primeiro envio
    if (!state.sessionId && window.Sessions) {
      state.sessionId = await window.Sessions.ensureSession();
    }

    appendUserMessage(text);

    if (window._useSSE) {
      sendMessageSSE(text);
    } else {
      sendMessageJSON(text);
    }
  }

  // ===== Envio JSON (modo padrão) =====
  async function sendMessageJSON(text) {
    var typingId = appendTypingIndicator();

    try {
      var res = await API.chat(text, state.history, state.sessionId);
      removeMessage(typingId);

      if (!res.ok) {
        var msg = res.status === 429
          ? 'Muitas perguntas em pouco tempo. Aguarde um momento e tente novamente.'
          : 'Erro ao processar sua pergunta. Tente novamente.';
        appendErrorMessage(msg);
        return;
      }

      var data = await res.json();
      appendBotMessage(data);

      state.history.push({ role: 'user',      content: text });
      state.history.push({ role: 'assistant', content: data.text || '' });
      if (state.history.length > 12) state.history = state.history.slice(-12);

      if (window.Sessions) window.Sessions.refreshList();

    } catch (_) {
      removeMessage(typingId);
      appendErrorMessage('Não foi possível conectar ao servidor. Verifique sua conexão.');
    } finally {
      state.isLoading = false;
      sendButton.disabled = false;
      inputField.focus();
      scrollToBottom();
    }
  }

  // ===== Envio SSE (multi-agente com progresso em tempo real) =====
  function sendMessageSSE(text) {
    var progressId = appendProgressIndicator();

    var body = JSON.stringify({
      message: text,
      history: state.history,
      session_id: state.sessionId,
    });

    fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body,
    }).then(function (response) {
      if (!response.ok) {
        removeMessage(progressId);
        var errorMsg = response.status === 429
          ? 'Muitas perguntas em pouco tempo. Aguarde um momento e tente novamente.'
          : 'Erro ao processar sua pergunta. Tente novamente.';
        appendErrorMessage(errorMsg);
        state.isLoading = false;
        sendButton.disabled = false;
        inputField.focus();
        return;
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      function processChunk(value) {
        buffer += decoder.decode(value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop(); // última linha pode estar incompleta

        var eventType = null;
        var dataLine = null;

        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            dataLine = line.slice(6).trim();
          } else if (line === '') {
            // Evento completo
            if (eventType && dataLine) {
              handleSSEEvent(eventType, dataLine, progressId, text);
              eventType = null;
              dataLine = null;
            }
          }
        }
      }

      function pump() {
        reader.read().then(function (result) {
          if (result.done) {
            state.isLoading = false;
            sendButton.disabled = false;
            inputField.focus();
            return;
          }
          processChunk(result.value);
          pump();
        }).catch(function () {
          removeMessage(progressId);
          appendErrorMessage('Conexão interrompida. Tente novamente.');
          state.isLoading = false;
          sendButton.disabled = false;
          inputField.focus();
        });
      }

      pump();

    }).catch(function () {
      removeMessage(progressId);
      appendErrorMessage('Não foi possível conectar ao servidor. Verifique sua conexão.');
      state.isLoading = false;
      sendButton.disabled = false;
      inputField.focus();
    });
  }

  // ===== Processa evento SSE individual =====
  function handleSSEEvent(eventType, dataStr, progressId, originalText) {
    var data = {};
    try { data = JSON.parse(dataStr); } catch (_) { return; }

    switch (eventType) {
      case 'searching':
        updateProgressIndicator(progressId, 'searching', data.message || 'Buscando informações...');
        break;
      case 'analyzing':
        var tablesFound = data.tables_found || 0;
        var msg = tablesFound > 0
          ? 'Analisando ' + tablesFound + ' tabela' + (tablesFound > 1 ? 's' : '') + '...'
          : (data.message || 'Analisando dados...');
        updateProgressIndicator(progressId, 'analyzing', msg);
        break;
      case 'tool_call':
        updateProgressSubtext(progressId, 'Calculando ' + (data.tool || '') + '...');
        break;
      case 'writing':
        updateProgressIndicator(progressId, 'writing', 'Preparando resposta...');
        break;
      case 'done':
        removeMessage(progressId);
        appendBotMessage(data);
        state.history.push({ role: 'user',      content: originalText });
        state.history.push({ role: 'assistant', content: data.text || '' });
        if (state.history.length > 12) state.history = state.history.slice(-12);
        if (window.Sessions) window.Sessions.refreshList();
        scrollToBottom();
        break;
      case 'error':
        removeMessage(progressId);
        appendErrorMessage(data.message || 'Erro ao processar sua pergunta. Tente novamente.');
        break;
    }
  }

  // ===== Indicador de progresso SSE =====
  var _PROGRESS_ICONS = {
    searching: '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>',
    analyzing: '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 10h18M3 14h18M10 4v16M14 4v16"/></svg>',
    writing:   '<svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>',
  };

  function appendProgressIndicator() {
    var id = 'progress-' + Date.now();
    var row = document.createElement('div');
    row.className = 'flex gap-3.5 items-start message-enter';
    row.id = id;

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center flex-shrink-0 shadow-lg shadow-brand-600/20 ring-2 ring-white';
    avatar.innerHTML = ICONS.scale;

    var card = document.createElement('div');
    card.className = 'bg-white rounded-2xl rounded-tl-none shadow-md shadow-gray-200/60 border border-gray-200 px-4 py-3 min-w-[200px]';

    var inner = document.createElement('div');
    inner.className = 'progress-indicator';
    inner.innerHTML = '<div class="progress-step searching">'
      + _PROGRESS_ICONS.searching
      + '<span class="progress-label">Buscando informações...</span>'
      + '</div>'
      + '<div class="progress-subtext"></div>';

    card.appendChild(inner);
    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
    return id;
  }

  function updateProgressIndicator(id, step, label) {
    var el = document.getElementById(id);
    if (!el) return;
    var inner = el.querySelector('.progress-indicator');
    if (!inner) return;
    var icon = _PROGRESS_ICONS[step] || '';
    inner.querySelector('.progress-step').className = 'progress-step ' + step;
    inner.querySelector('.progress-step').innerHTML = icon + '<span class="progress-label">' + Utils.escapeHtml(label) + '</span>';
    scrollToBottom();
  }

  function updateProgressSubtext(id, text) {
    var el = document.getElementById(id);
    if (!el) return;
    var subtext = el.querySelector('.progress-subtext');
    if (subtext) subtext.textContent = text;
  }

  // ===== Scroll =====
  function scrollToBottom() {
    requestAnimationFrame(function () {
      messagesArea.scrollTo({ top: messagesArea.scrollHeight, behavior: 'smooth' });
    });
  }

  // ===== Mensagem do usuário =====
  function appendUserMessage(text) {
    var row = document.createElement('div');
    row.className = 'flex gap-3 items-start justify-end message-enter';

    var bubble = document.createElement('div');
    bubble.className = 'max-w-[80%] bg-gradient-to-br from-brand-600 to-brand-500 text-white rounded-2xl rounded-tr-none px-4 py-3 text-sm leading-relaxed shadow-lg shadow-brand-600/20';
    bubble.textContent = text;

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-brand-50 flex items-center justify-center flex-shrink-0 ring-2 ring-white';
    avatar.innerHTML = ICONS.user;

    row.appendChild(bubble);
    row.appendChild(avatar);
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  // ===== Indicador de digitação =====
  function appendTypingIndicator() {
    var id = 'typing-' + Date.now();
    var row = document.createElement('div');
    row.className = 'flex gap-3.5 items-start message-enter';
    row.id = id;

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center flex-shrink-0 shadow-lg shadow-brand-600/20 ring-2 ring-white';
    avatar.innerHTML = ICONS.scale;

    var card = document.createElement('div');
    card.className = 'bg-white rounded-2xl rounded-tl-none shadow-md shadow-gray-200/60 border border-gray-200 px-4 py-3';

    var indicator = document.createElement('div');
    indicator.className = 'typing-indicator';
    for (var i = 0; i < 3; i++) {
      var dot = document.createElement('div');
      dot.className = 'typing-dot';
      indicator.appendChild(dot);
    }

    card.appendChild(indicator);
    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
    return id;
  }

  function removeMessage(id) {
    var el = document.getElementById(id);
    if (el) el.remove();
  }

  // ===== Mensagem de erro =====
  function appendErrorMessage(msg) {
    var row = document.createElement('div');
    row.className = 'flex gap-3.5 items-start message-enter';

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center flex-shrink-0 shadow-lg shadow-brand-600/20 ring-2 ring-white';
    avatar.innerHTML = ICONS.scale;

    var card = document.createElement('div');
    card.className = 'flex-1 min-w-0 bg-white rounded-2xl rounded-tl-none shadow-md shadow-gray-200/60 border border-gray-200 p-5';
    card.innerHTML = '<div class="error-notice">' + ICONS.alertCircle + '<span>' + Utils.escapeHtml(msg) + '</span></div>';

    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  // ===== Mensagem do bot =====
  function appendBotMessage(data) {
    var row = document.createElement('div');
    row.className = 'flex gap-3.5 items-start message-enter';

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center flex-shrink-0 shadow-lg shadow-brand-600/20 ring-2 ring-white';
    avatar.innerHTML = ICONS.scale;

    row.appendChild(avatar);
    row.appendChild(buildBotCard(data));
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  // ===== Construção do card do bot =====
  function buildBotCard(data) {
    var card = document.createElement('div');
    card.className = 'flex-1 min-w-0 bg-white rounded-2xl rounded-tl-none shadow-md shadow-gray-200/60 border border-gray-200 overflow-hidden';

    // Mapeia sources (novo formato) → citations (formato do renderer)
    var rawSources  = data.sources     || [];
    var citations   = rawSources.map(function (s, i) {
      return { number: i + 1, title: s.document_title, url: s.document_url, snippet: s.snippet };
    });
    var tables      = data.tables      || [];  // [{title, headers[], rows[][]}]
    var queryId     = data.query_id    || null;

    // ── Header ──
    var header = document.createElement('div');
    header.className = 'px-5 pt-4 pb-2';
    var label = data.category ? data.category.toUpperCase() : 'RESPOSTA';
    header.innerHTML = '<span class="inline-flex items-center gap-2 text-[11px] font-bold tracking-widest text-brand-600 uppercase">'
      + ICONS.fileText + ' ' + Utils.escapeHtml(label) + '</span>';
    card.appendChild(header);

    // ── Metrics cards (KPIs) ──
    var metricsData = data.metrics || [];
    if (metricsData.length) {
      var metricsHtml = Utils.renderMetricsCards(metricsData);
      if (metricsHtml) {
        var metricsWrap = document.createElement('div');
        metricsWrap.className = 'card-section';
        metricsWrap.innerHTML = metricsHtml;
        card.appendChild(metricsWrap);
      }
    }

    // ── Texto principal (com citações inline) ──
    if (data.text) {
      var boxWrap = document.createElement('div');
      boxWrap.className = 'card-section';
      var box = document.createElement('div');
      box.className = 'content-box';
      box.innerHTML = Utils.markdownToHtml(data.text, citations);
      boxWrap.appendChild(box);
      card.appendChild(boxWrap);
    }

    // ── Tabelas estruturadas (field tables[]) ──
    if (tables.length) {
      var tablesHtml = Utils.renderTablesSection(tables);
      if (tablesHtml) {
        var tablesWrap = document.createElement('div');
        tablesWrap.className = 'card-section extracted-content';
        tablesWrap.innerHTML = tablesHtml;
        card.appendChild(tablesWrap);
      }
    }

    // ── Seção de citações / fontes (após tabelas) ──
    if (citations.length) {
      var citeHtml = Utils.buildCitationsSection(citations);
      if (citeHtml) {
        var citeWrap = document.createElement('div');
        citeWrap.className = 'card-section';
        citeWrap.innerHTML = citeHtml;
        card.appendChild(citeWrap);
      }
    }

    // ── Sugestões ──
    if (data.suggestions && data.suggestions.length) {
      var sugDiv = document.createElement('div');
      sugDiv.className = 'card-section suggestions-wrap';
      data.suggestions.forEach(function (s) { sugDiv.appendChild(createChip(s)); });
      card.appendChild(sugDiv);
    }

    // ── Feedback bar ──
    card.appendChild(buildFeedbackBar(queryId));

    return card;
  }

  // ===== Feedback bar =====
  function buildFeedbackBar(queryId) {
    var bar = document.createElement('div');
    bar.className = 'feedback-bar';

    var label = document.createElement('span');
    label.className = 'feedback-label';
    label.textContent = 'Esta resposta foi útil?';
    bar.appendChild(label);

    var btnUp   = buildFeedbackBtn('positive', ICONS.thumbUp,   'Resposta útil');
    var btnDown = buildFeedbackBtn('negative', ICONS.thumbDown, 'Resposta não foi útil');

    function activate(clicked, other, value) {
      if (clicked.classList.contains('feedback-active')) {
        // deselect
        clicked.classList.remove('feedback-active', 'feedback-positive', 'feedback-negative');
        return;
      }
      clicked.classList.add('feedback-active', 'feedback-' + value);
      other.classList.remove('feedback-active', 'feedback-positive', 'feedback-negative');
      API.sendFeedback(queryId, value);
    }

    btnUp.addEventListener('click', function ()   { activate(btnUp,   btnDown, 'positive'); });
    btnDown.addEventListener('click', function ()  { activate(btnDown, btnUp,   'negative'); });

    bar.appendChild(btnUp);
    bar.appendChild(btnDown);
    return bar;
  }

  function buildFeedbackBtn(value, iconHtml, ariaLabel) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'feedback-btn';
    btn.setAttribute('aria-label', ariaLabel);
    btn.setAttribute('data-feedback', value);
    btn.innerHTML = iconHtml;
    return btn;
  }

  // ===== Link item =====
  function buildLinkItem(lk) {
    var a = document.createElement('a');
    a.href = lk.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.className = 'link-item-anchor';
    var iconKey = LINK_TYPE_ICON[lk.type] || 'page';
    a.innerHTML = (ICONS[iconKey] || ICONS.page)
      + '<span class="flex-1">' + Utils.escapeHtml(lk.title) + '</span>'
      + ICONS.externalLink;
    return a;
  }

  // ===== Chip de sugestão =====
  function createChip(text) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'suggestion-chip';
    btn.textContent = text;
    btn.addEventListener('click', function () {
      if (!state.isLoading) sendMessage(text);
    });
    return btn;
  }

  // ===== API Pública =====
  window.Chat = {
    loadHistory:     loadHistory,
    sendWithContext: sendWithContext,
  };

  // ===== Bootstrap =====
  document.addEventListener('DOMContentLoaded', init);
})();
