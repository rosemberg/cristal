/* Transparência Chat — Frontend JS */
(function () {
  'use strict';

  // ===== SVG Icons (inline, no external deps) =====
  var ICONS = {
    scale: '<svg class="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3"/></svg>',
    user: '<svg class="w-5 h-5 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>',
    fileText: '<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
    link: '<svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>',
    externalLink: '<svg class="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>',
    search: '<svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>',
    chevronDown: '<svg class="w-4 h-4 accordion-arrow transition-transform duration-200" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>',
    alertCircle: '<svg class="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    download: '<svg class="w-4 h-4 flex-shrink-0 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
    play: '<svg class="w-4 h-4 flex-shrink-0 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    code: '<svg class="w-4 h-4 flex-shrink-0 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>',
    page: '<svg class="w-4 h-4 flex-shrink-0 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>',
  };

  var LINK_TYPE_ICON = {
    page: 'page',
    pdf: 'download',
    csv: 'download',
    video: 'play',
    audio: 'play',
    api: 'code',
    external: 'externalLink',
  };

  var state = {
    history: [],
    isLoading: false,
  };

  // ===== DOM REFS =====
  var messagesArea = document.getElementById('messages-area');
  var inputField = document.getElementById('input-field');
  var sendButton = document.getElementById('send-button');

  // ===== INIT =====
  function init() {
    loadInitialSuggestions();
    inputField.addEventListener('keydown', handleKeydown);
    sendButton.addEventListener('click', handleSend);
    inputField.addEventListener('input', autoResize);

    // Bind initial welcome chips
    document.querySelectorAll('#welcome-chips .suggestion-chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!state.isLoading) sendMessage(btn.textContent);
      });
    });
  }

  async function loadInitialSuggestions() {
    try {
      var res = await fetch('/api/suggest');
      if (!res.ok) return;
      var data = await res.json();
      var welcomeChips = document.getElementById('welcome-chips');
      if (welcomeChips && data.suggestions) {
        welcomeChips.innerHTML = '';
        data.suggestions.forEach(function (s) {
          welcomeChips.appendChild(createChip(s));
        });
      }
    } catch (_) {
      // silently ignore
    }
  }

  // ===== EVENT HANDLERS =====
  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
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

  // ===== MAIN SEND =====
  async function sendMessage(text) {
    state.isLoading = true;
    sendButton.disabled = true;
    inputField.value = '';
    inputField.style.height = 'auto';

    appendUserMessage(text);
    var typingId = appendTypingIndicator();

    try {
      var res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history: state.history }),
      });

      removeMessage(typingId);

      if (!res.ok) {
        if (res.status === 429) {
          appendErrorMessage('Muitas perguntas em pouco tempo. Aguarde um momento e tente novamente.');
        } else {
          appendErrorMessage('Erro ao processar sua pergunta. Tente novamente.');
        }
        return;
      }

      var data = await res.json();
      appendBotMessage(data);

      state.history.push({ role: 'user', content: text });
      state.history.push({ role: 'assistant', content: data.text || '' });

      if (state.history.length > 12) {
        state.history = state.history.slice(-12);
      }

    } catch (err) {
      removeMessage(typingId);
      appendErrorMessage('Não foi possível conectar ao servidor. Verifique sua conexão.');
    } finally {
      state.isLoading = false;
      sendButton.disabled = false;
      inputField.focus();
      scrollToBottom();
    }
  }

  // ===== RENDER HELPERS =====

  function scrollToBottom() {
    requestAnimationFrame(function () {
      messagesArea.scrollTo({ top: messagesArea.scrollHeight, behavior: 'smooth' });
    });
  }

  // -- User message --
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
    return row;
  }

  // -- Typing indicator --
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

  // -- Bot message --
  function appendBotMessage(data) {
    var row = document.createElement('div');
    row.className = 'flex gap-3.5 items-start message-enter';

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center flex-shrink-0 shadow-lg shadow-brand-600/20 ring-2 ring-white';
    avatar.innerHTML = ICONS.scale;

    var card = buildBotCard(data);

    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  // -- Error message --
  function appendErrorMessage(msg) {
    var row = document.createElement('div');
    row.className = 'flex gap-3.5 items-start message-enter';

    var avatar = document.createElement('div');
    avatar.className = 'w-10 h-10 rounded-full bg-gradient-to-br from-brand-600 to-brand-400 flex items-center justify-center flex-shrink-0 shadow-lg shadow-brand-600/20 ring-2 ring-white';
    avatar.innerHTML = ICONS.scale;

    var card = document.createElement('div');
    card.className = 'flex-1 min-w-0 bg-white rounded-2xl rounded-tl-none shadow-md shadow-gray-200/60 border border-gray-200 p-5';

    var err = document.createElement('div');
    err.className = 'error-notice';
    err.innerHTML = ICONS.alertCircle + '<span>' + escapeHtml(msg) + '</span>';

    card.appendChild(err);
    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  // -- Build bot card --
  function buildBotCard(data) {
    var card = document.createElement('div');
    card.className = 'flex-1 min-w-0 bg-white rounded-2xl rounded-tl-none shadow-md shadow-gray-200/60 border border-gray-200 overflow-hidden';

    // Header
    var header = document.createElement('div');
    header.className = 'px-5 pt-4 pb-2';
    var label = data.category ? data.category.toUpperCase() : 'RESPOSTA';
    header.innerHTML = '<span class="inline-flex items-center gap-2 text-[11px] font-bold tracking-widest text-brand-600 uppercase">' + ICONS.fileText + ' ' + escapeHtml(label) + '</span>';
    card.appendChild(header);

    // Main text box
    if (data.text) {
      var boxWrap = document.createElement('div');
      boxWrap.className = 'px-5 pb-4';
      var box = document.createElement('div');
      box.className = 'content-box bg-surface border border-surface-border/60 rounded-xl p-4 text-sm leading-relaxed text-gray-700';
      box.innerHTML = markdownToHtml(data.text);
      boxWrap.appendChild(box);
      card.appendChild(boxWrap);
    }

    // Links
    if (data.links && data.links.length > 0) {
      var section = document.createElement('div');
      section.className = 'px-5 pb-4 space-y-1.5';

      var lbl = document.createElement('div');
      lbl.className = 'flex items-center gap-1.5 text-xs font-semibold text-gray-500 mb-1';
      lbl.innerHTML = ICONS.link + ' <span>Links úteis</span>';
      section.appendChild(lbl);

      var ul = document.createElement('ul');
      ul.className = 'space-y-0.5';
      data.links.forEach(function (lk) {
        var li = document.createElement('li');
        li.appendChild(buildLink(lk));
        ul.appendChild(li);
      });
      section.appendChild(ul);
      card.appendChild(section);
    }

    // Extracted content accordion
    if (data.extracted_content) {
      var accWrap = document.createElement('div');
      accWrap.className = 'px-5 pb-4';

      var accordion = document.createElement('div');
      accordion.className = 'accordion border border-gray-200 rounded-xl overflow-hidden';

      var toggle = document.createElement('button');
      toggle.className = 'w-full bg-gray-50 hover:bg-gray-100 px-4 py-3 flex items-center justify-between text-sm font-medium text-brand-600 cursor-pointer transition-colors duration-150';
      toggle.innerHTML = '<span class="flex items-center gap-2">' + ICONS.search + ' Ver conteúdo da página</span>' + ICONS.chevronDown;
      toggle.addEventListener('click', function () { accordion.classList.toggle('open'); });

      var body = document.createElement('div');
      body.className = 'accordion-body extracted-content text-sm leading-relaxed text-gray-600 border-t border-gray-200 bg-white break-words';
      body.innerHTML = formatExtractedContent(data.extracted_content);

      accordion.appendChild(toggle);
      accordion.appendChild(body);
      accWrap.appendChild(accordion);
      card.appendChild(accWrap);
    }

    // Suggestions
    if (data.suggestions && data.suggestions.length > 0) {
      var sugDiv = document.createElement('div');
      sugDiv.className = 'px-5 pb-5 flex flex-wrap gap-2';
      data.suggestions.forEach(function (s) { sugDiv.appendChild(createChip(s)); });
      card.appendChild(sugDiv);
    }

    return card;
  }

  function buildLink(lk) {
    var a = document.createElement('a');
    a.href = lk.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.className = 'link-item-anchor';

    var iconKey = LINK_TYPE_ICON[lk.type] || 'page';
    a.innerHTML = (ICONS[iconKey] || ICONS.page)
      + '<span class="flex-1">' + escapeHtml(lk.title) + '</span>'
      + ICONS.externalLink;

    return a;
  }

  function createChip(text) {
    var btn = document.createElement('button');
    btn.className = 'suggestion-chip';
    btn.textContent = text;
    btn.addEventListener('click', function () {
      if (!state.isLoading) sendMessage(text);
    });
    return btn;
  }

  // ===== UTILITIES =====

  /**
   * Formata o conteúdo extraído para exibição rica:
   * - Remove linhas de ruído (compartilhamento, logos)
   * - Detecta padrões tabulares (Ano/Período/Formato) e renderiza como tabela
   * - Converte markdown (bold, italic, links, tabelas) para HTML
   * - Converte URLs em links clicáveis
   * - Agrupa itens de texto em lista estruturada
   */
  function formatExtractedContent(raw) {
    var lines = raw.split('\n');
    var noisePatterns = [
      /^compartilhar\s+p[áa]gina?\s+via/i,
      /@@site-logo/i,
      /^\s*$/,
    ];

    var filtered = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      var isNoise = false;
      for (var j = 0; j < noisePatterns.length; j++) {
        if (noisePatterns[j].test(line)) { isNoise = true; break; }
      }
      if (!isNoise) filtered.push(line);
    }

    if (filtered.length === 0) return '<p class="text-gray-400">Sem conteúdo disponível.</p>';

    // Encontrar onde começa a parte tabular (se houver)
    var tableStart = findTableStart(filtered);
    var introLines = (tableStart >= 0) ? filtered.slice(0, tableStart) : filtered;
    var dataLines = (tableStart >= 0) ? filtered.slice(tableStart) : [];

    // Renderizar parte introdutória
    var htmlParts = [];
    var listItems = [];

    for (var k = 0; k < introLines.length; k++) {
      var text = introLines[k];
      var isUrl = /^https?:\/\/\S+$/i.test(text);

      if (isUrl) {
        if (listItems.length > 0) {
          htmlParts.push('<ul class="ec-list">' + listItems.join('') + '</ul>');
          listItems = [];
        }
        var displayUrl = text.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
        if (displayUrl.length > 60) displayUrl = displayUrl.substring(0, 57) + '...';
        htmlParts.push(
          '<a href="' + escapeHtml(text) + '" target="_blank" rel="noopener noreferrer" class="ec-link">'
          + ICONS.externalLink
          + '<span>' + escapeHtml(displayUrl) + '</span></a>'
        );
      } else if (k === 0 || (k === 1 && introLines[0].length < 60)) {
        if (listItems.length > 0) {
          htmlParts.push('<ul class="ec-list">' + listItems.join('') + '</ul>');
          listItems = [];
        }
        var tag = (k === 0) ? 'h4' : 'p';
        var cls = (k === 0) ? 'ec-title' : 'ec-description';
        htmlParts.push('<' + tag + ' class="' + cls + '">' + inlineMarkdown(text) + '</' + tag + '>');
      } else {
        listItems.push('<li>' + inlineMarkdown(text) + '</li>');
      }
    }

    if (listItems.length > 0) {
      htmlParts.push('<ul class="ec-list">' + listItems.join('') + '</ul>');
    }

    // Renderizar parte tabular (se detectada)
    if (dataLines.length > 0) {
      var tableHtml = tryBuildTable(dataLines);
      if (tableHtml) htmlParts.push(tableHtml);
    }

    // Fallback: tabela markdown ( | col | col | )
    if (dataLines.length === 0) {
      var mdTableHtml = tryBuildMarkdownTable(filtered);
      if (mdTableHtml) return mdTableHtml;
    }

    return htmlParts.join('');
  }

  /** Aplica formatação markdown inline (bold, italic, code, links) */
  function inlineMarkdown(text) {
    var html = escapeHtml(text);
    // Links markdown: [texto](url)
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer" class="ec-inline-link">$1</a>');
    // URLs puras inline
    html = html.replace(/(https?:\/\/[^\s&lt;]+)/g, function (url) {
      var decoded = url.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
      return '<a href="' + decoded + '" target="_blank" rel="noopener noreferrer" class="ec-inline-link">' + decoded + '</a>';
    });
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Inline code
    html = html.replace(/`(.+?)`/g, '<code>$1</code>');
    return html;
  }

  /** Regex helpers para detecção tabular */
  var TABLE_MONTHS = /^(JANEIRO|FEVEREIRO|MAR[CÇ]O|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)$/i;
  var TABLE_FORMATS = /^(PDF|XLSX?|CSV|ODS|DOCX?|ODT|RTF|ZIP|RAR)$/i;
  var TABLE_YEAR = /^\d{4}$/;
  var TABLE_HEADER = /^(ano|per[ií]odo|m[eê]s|formato|tipo|arquivo|download)$/i;

  /**
   * Encontra o índice onde começa a zona tabular no array de linhas.
   * Procura a primeira linha que é um cabeçalho de tabela ("Ano", "Período")
   * ou ano (4 dígitos) precedido por padrões tabulares.
   * Retorna -1 se não encontrar padrão tabular.
   */
  function findTableStart(lines) {
    // Contar anos e meses para decidir se há tabela
    var yearCount = 0, monthCount = 0;
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i].trim();
      if (TABLE_YEAR.test(l)) yearCount++;
      else if (TABLE_MONTHS.test(l)) monthCount++;
    }
    if (yearCount < 2 || monthCount < 2) return -1;

    // Buscar o início: primeira linha de header tabular ou primeiro ano,
    // incluindo linhas de contexto como "Arquivos em formato pdf."
    for (var j = 0; j < lines.length; j++) {
      var line = lines[j].trim();
      if (TABLE_HEADER.test(line)) {
        // Voltar para incluir linha descritiva anterior (ex: "Arquivos em formato pdf.")
        var start = j;
        if (start > 0 && /^arquivos?\s+em\s+formato/i.test(lines[start - 1].trim())) {
          start = start - 1;
        }
        return start;
      }
      if (TABLE_YEAR.test(line)) {
        // Voltar para capturar headers e texto descritivo adjacente
        var start2 = j;
        while (start2 > 0) {
          var prev = lines[start2 - 1].trim();
          if (TABLE_HEADER.test(prev) || /^arquivos?\s+em\s+formato/i.test(prev)) {
            start2--;
          } else {
            break;
          }
        }
        return start2;
      }
    }
    return -1;
  }

  /**
   * Constrói tabela HTML a partir de linhas com padrão Ano/Período/Formato.
   * Recebe apenas as linhas da zona tabular (sem texto introdutório).
   */
  function tryBuildTable(lines) {
    var rows = [];
    var currentYear = null;
    var currentMonth = null;
    var currentFormats = [];
    var contextLines = [];

    function flushRow() {
      if (currentYear && currentMonth) {
        rows.push({
          ano: currentYear,
          periodo: currentMonth,
          formatos: currentFormats.slice()
        });
      }
      currentFormats = [];
    }

    for (var j = 0; j < lines.length; j++) {
      var line = lines[j].trim();
      if (TABLE_HEADER.test(line)) continue;
      if (TABLE_YEAR.test(line)) {
        flushRow();
        currentYear = line;
        currentMonth = null;
        continue;
      }
      if (TABLE_MONTHS.test(line)) {
        flushRow();
        currentMonth = line.charAt(0).toUpperCase() + line.slice(1).toLowerCase();
        continue;
      }
      if (TABLE_FORMATS.test(line)) {
        currentFormats.push(line.toUpperCase());
        continue;
      }
      // Linhas de contexto (ex: "Arquivos em formato pdf.")
      if (!currentYear) {
        contextLines.push(line);
      }
    }
    flushRow();

    if (rows.length < 2) return null;

    var hasFormats = rows.some(function (r) { return r.formatos.length > 0; });

    var html = '';
    if (contextLines.length > 0) {
      html += '<p class="ec-description">' + escapeHtml(contextLines.join(' ')) + '</p>';
    }

    html += '<div class="ec-table-wrapper"><table class="ec-table">';
    html += '<thead><tr><th>Ano</th><th>Período</th>';
    if (hasFormats) html += '<th>Formatos</th>';
    html += '</tr></thead><tbody>';

    var prevYear = null;
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r];
      html += '<tr>';
      if (row.ano !== prevYear) {
        var span = 0;
        for (var s = r; s < rows.length && rows[s].ano === row.ano; s++) span++;
        html += '<td class="ec-table-year" rowspan="' + span + '">' + escapeHtml(row.ano) + '</td>';
        prevYear = row.ano;
      }
      html += '<td>' + escapeHtml(row.periodo) + '</td>';
      if (hasFormats) {
        var badges = row.formatos.map(function (f) {
          var cls = f === 'PDF' ? 'ec-badge-pdf' : (f === 'XLSX' || f === 'XLS') ? 'ec-badge-xlsx' : 'ec-badge-other';
          return '<span class="ec-badge ' + cls + '">' + escapeHtml(f) + '</span>';
        }).join(' ');
        html += '<td>' + (badges || '—') + '</td>';
      }
      html += '</tr>';
    }

    html += '</tbody></table></div>';
    return html;
  }

  /** Detecta e renderiza tabelas em formato markdown ( | col | col | ) */
  function tryBuildMarkdownTable(lines) {
    // Procurar sequência de linhas com pipes
    var tableLines = [];
    var nonTableLines = [];
    var inTable = false;

    for (var i = 0; i < lines.length; i++) {
      if (/^\|.+\|$/.test(lines[i].trim())) {
        inTable = true;
        tableLines.push(lines[i].trim());
      } else {
        if (inTable) break;
        nonTableLines.push(lines[i]);
      }
    }

    if (tableLines.length < 2) return null;

    var html = '';
    if (nonTableLines.length > 0) {
      html += '<h4 class="ec-title">' + escapeHtml(nonTableLines[0]) + '</h4>';
    }

    html += '<div class="ec-table-wrapper"><table class="ec-table">';

    for (var j = 0; j < tableLines.length; j++) {
      var cells = tableLines[j].split('|').filter(function (c) { return c.trim() !== ''; });
      // Pular linha separadora (|---|---|)
      if (/^[\s\-:]+$/.test(cells[0])) continue;

      var cellTag = (j === 0) ? 'th' : 'td';
      var rowTag = (j === 0) ? 'thead' : '';
      if (j === 0) html += '<thead>';
      if (j === 1 || (j === 2 && /^[\s\-:]+$/.test(tableLines[1].split('|').filter(function (c) { return c.trim() !== ''; })[0]))) {
        html += '<tbody>';
      }
      html += '<tr>';
      for (var c = 0; c < cells.length; c++) {
        html += '<' + cellTag + '>' + inlineMarkdown(cells[c].trim()) + '</' + cellTag + '>';
      }
      html += '</tr>';
      if (j === 0) html += '</thead>';
    }

    html += '</tbody></table></div>';
    return html;
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function markdownToHtml(text) {
    var html = escapeHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/`(.+?)`/g, '<code>$1</code>');
    html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
    html = html.replace(/\n\n+/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';
    html = html.replace(/<p><\/p>/g, '');
    return html;
  }

  // ===== BOOTSTRAP =====
  document.addEventListener('DOMContentLoaded', init);
})();
