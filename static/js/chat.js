/* Transparência Chat — Frontend JS */
(function () {
  'use strict';

  const state = {
    history: [],
    isLoading: false,
  };

  const LINK_ICONS = {
    page: '🔗',
    pdf: '📄',
    csv: '📊',
    video: '🎬',
    audio: '🎵',
    api: '🔧',
    external: '↗️',
  };

  // ===== DOM REFS =====
  const messagesArea = document.getElementById('messages-area');
  const inputField = document.getElementById('input-field');
  const sendButton = document.getElementById('send-button');

  // ===== INIT =====
  async function init() {
    loadInitialSuggestions();
    inputField.addEventListener('keydown', handleKeydown);
    sendButton.addEventListener('click', handleSend);
    inputField.addEventListener('input', autoResize);
  }

  async function loadInitialSuggestions() {
    try {
      const res = await fetch('/api/suggest');
      if (!res.ok) return;
      const data = await res.json();
      const welcomeChips = document.getElementById('welcome-chips');
      if (welcomeChips && data.suggestions) {
        welcomeChips.innerHTML = '';
        data.suggestions.forEach(s => {
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
    const text = inputField.value.trim();
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

    const typingId = appendTypingIndicator();

    try {
      const res = await fetch('/api/chat', {
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

      const data = await res.json();
      appendBotMessage(data);

      // Update history
      state.history.push({ role: 'user', content: text });
      state.history.push({ role: 'assistant', content: data.text || '' });

      // Keep last 12 entries (6 pairs)
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
    messagesArea.scrollTo({ top: messagesArea.scrollHeight, behavior: 'smooth' });
  }

  function appendUserMessage(text) {
    const row = document.createElement('div');
    row.className = 'message-row user-row';

    const avatar = document.createElement('div');
    avatar.className = 'avatar user-avatar';
    avatar.textContent = '👤';

    const bubble = document.createElement('div');
    bubble.className = 'user-bubble';
    bubble.textContent = text;

    row.appendChild(bubble);
    row.appendChild(avatar);
    messagesArea.appendChild(row);
    scrollToBottom();
    return row;
  }

  function appendTypingIndicator() {
    const id = 'typing-' + Date.now();
    const row = document.createElement('div');
    row.className = 'message-row';
    row.id = id;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = '⚖️';

    const card = document.createElement('div');
    card.className = 'bot-card';

    const indicator = document.createElement('div');
    indicator.className = 'typing-indicator';
    for (let i = 0; i < 3; i++) {
      const dot = document.createElement('div');
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
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function appendBotMessage(data) {
    const row = document.createElement('div');
    row.className = 'message-row';

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = '⚖️';

    const card = buildBotCard(data);

    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  function appendErrorMessage(msg) {
    const row = document.createElement('div');
    row.className = 'message-row';

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = '⚖️';

    const card = document.createElement('div');
    card.className = 'bot-card';

    const err = document.createElement('div');
    err.className = 'error-notice';
    err.textContent = msg;

    card.appendChild(err);
    row.appendChild(avatar);
    row.appendChild(card);
    messagesArea.appendChild(row);
    scrollToBottom();
  }

  function buildBotCard(data) {
    const card = document.createElement('div');
    card.className = 'bot-card';

    // Header
    const header = document.createElement('div');
    header.className = 'bot-card-header';
    const label = data.category ? data.category.toUpperCase() : 'RESPOSTA';
    header.innerHTML = '<span>📋</span> ' + escapeHtml(label);
    card.appendChild(header);

    // Main text box
    if (data.text) {
      const box = document.createElement('div');
      box.className = 'content-box';
      box.innerHTML = markdownToHtml(data.text);
      card.appendChild(box);
    }

    // Links
    if (data.links && data.links.length > 0) {
      const section = document.createElement('div');
      section.className = 'links-section';

      const label = document.createElement('div');
      label.className = 'links-label';
      label.innerHTML = '🔗 Links úteis:';
      section.appendChild(label);

      const ul = document.createElement('ul');
      ul.className = 'link-list';
      data.links.forEach(lk => {
        const li = document.createElement('li');
        li.className = 'link-item';
        li.appendChild(buildLink(lk));
        ul.appendChild(li);
      });
      section.appendChild(ul);
      card.appendChild(section);
    }

    // Extracted content accordion
    if (data.extracted_content) {
      const accordion = document.createElement('div');
      accordion.className = 'accordion';

      const toggle = document.createElement('button');
      toggle.className = 'accordion-toggle';
      toggle.innerHTML = '<span>📄 Ver conteúdo da página</span><span class="accordion-arrow">▼</span>';
      toggle.addEventListener('click', () => accordion.classList.toggle('open'));

      const body = document.createElement('div');
      body.className = 'accordion-body';
      body.textContent = data.extracted_content;

      accordion.appendChild(toggle);
      accordion.appendChild(body);
      card.appendChild(accordion);
    }

    // Suggestions
    if (data.suggestions && data.suggestions.length > 0) {
      const sugDiv = document.createElement('div');
      sugDiv.className = 'suggestions';
      data.suggestions.forEach(s => sugDiv.appendChild(createChip(s)));
      card.appendChild(sugDiv);
    }

    return card;
  }

  function buildLink(lk) {
    const a = document.createElement('a');
    a.href = lk.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';

    const icon = document.createElement('span');
    icon.className = 'link-icon';
    icon.textContent = LINK_ICONS[lk.type] || '🔗';

    const title = document.createElement('span');
    title.textContent = lk.title;

    const ext = document.createElement('span');
    ext.className = 'link-external';
    ext.textContent = '↗';

    a.appendChild(icon);
    a.appendChild(title);
    a.appendChild(ext);
    return a;
  }

  function createChip(text) {
    const btn = document.createElement('button');
    btn.className = 'suggestion-chip';
    btn.textContent = text;
    btn.addEventListener('click', () => {
      if (!state.isLoading) sendMessage(text);
    });
    return btn;
  }

  // ===== UTILITIES =====

  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function markdownToHtml(text) {
    // Escape HTML first
    let html = escapeHtml(text);

    // Bold **text**
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic *text*
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Inline code `text`
    html = html.replace(/`(.+?)`/g, '<code>$1</code>');
    // Unordered list items
    html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
    // Ordered list items
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    // Wrap consecutive <li> in <ul>
    html = html.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
    // Paragraphs (double newline)
    html = html.replace(/\n\n+/g, '</p><p>');
    // Single newlines
    html = html.replace(/\n/g, '<br>');
    // Wrap in paragraph
    html = '<p>' + html + '</p>';
    // Clean up empty paragraphs
    html = html.replace(/<p><\/p>/g, '');

    return html;
  }

  // ===== BOOTSTRAP =====
  document.addEventListener('DOMContentLoaded', init);
})();
