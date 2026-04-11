/* Cristal 2.0 — Documents: visualizador de documentos no painel direito (Etapa 14) */
/* Depende de: api.js (window.API), utils.js (window.Utils) */
(function (root) {
  'use strict';

  var DOC_TYPE_LABEL = { pdf: 'PDF', csv: 'CSV', xlsx: 'Excel', doc: 'Word', docx: 'Word' };
  var DOC_TYPE_COLOR = { pdf: '#DC2626', csv: '#059669', xlsx: '#2563EB', doc: '#7C3AED', docx: '#7C3AED' };

  // ===== Abrir documento pelo URL =====
  async function openDocument(docUrl, docMeta) {
    var panel    = document.getElementById('docs-panel');
    var panelTitle = document.getElementById('docs-panel-title');
    var panelBody  = document.getElementById('docs-panel-body');
    if (!panel || !panelBody) return;

    // Mostrar painel com loading
    var title = (docMeta && docMeta.title) ? docMeta.title : _filenameFromUrl(docUrl);
    if (panelTitle) panelTitle.textContent = title;
    panelBody.innerHTML = _buildLoadingSkeleton();
    panel.removeAttribute('hidden');

    // Buscar conteúdo
    try {
      var res = await API.getDocumentContent(docUrl);
      var content = null;
      var error   = false;

      if (res.ok) {
        var data = await res.json();
        content = data.content || null;
      } else {
        error = true;
      }

      panelBody.innerHTML = _buildDocumentPanel(docUrl, docMeta, content, error);
      _bindPanelTabs(panelBody);
    } catch (_) {
      panelBody.innerHTML = _buildErrorState();
    }
  }

  // ===== Abrir documento com apenas metadados (sem buscar conteúdo) =====
  function openDocumentMeta(docUrl, docMeta) {
    var panel    = document.getElementById('docs-panel');
    var panelTitle = document.getElementById('docs-panel-title');
    var panelBody  = document.getElementById('docs-panel-body');
    if (!panel || !panelBody) return;

    var title = (docMeta && docMeta.title) ? docMeta.title : _filenameFromUrl(docUrl);
    if (panelTitle) panelTitle.textContent = title;
    panelBody.innerHTML = _buildDocumentPanel(docUrl, docMeta, null, false);
    _bindPanelTabs(panelBody);
    panel.removeAttribute('hidden');
  }

  // ===== Construir painel =====
  function _buildDocumentPanel(docUrl, meta, content, error) {
    var type  = (meta && meta.type) ? meta.type.toLowerCase() : _inferType(docUrl);
    var label = DOC_TYPE_LABEL[type] || type.toUpperCase();
    var color = DOC_TYPE_COLOR[type] || '#6B7280';
    var title = (meta && meta.title) ? meta.title : _filenameFromUrl(docUrl);
    var pages = (meta && meta.num_pages) ? meta.num_pages + ' páginas' : '';

    var html = '<div class="doc-panel-meta">'
      + '<span class="doc-type-badge" style="background:' + color + '15;color:' + color + ';border-color:' + color + '30">' + Utils.escapeHtml(label) + '</span>'
      + (pages ? '<span class="doc-meta-info">' + Utils.escapeHtml(pages) + '</span>' : '')
      + '</div>';

    // Botões de ação
    html += '<div class="doc-actions">'
      + '<a href="' + Utils.escapeHtml(docUrl) + '" target="_blank" rel="noopener noreferrer" class="doc-action-btn doc-action-primary">'
      + '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>'
      + 'Abrir original</a>'
      + '<button type="button" class="doc-action-btn doc-action-secondary" onclick="window.Chat&&window.Chat.sendWithContext&&window.Chat.sendWithContext(\'' + Utils.escapeHtml(title).replace(/'/g, "\\'") + '\')">'
      + '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>'
      + 'Perguntar</button>'
      + '</div>';

    // Tabs
    var hasContent = content && content.length > 0;
    html += '<div class="doc-tabs" role="tablist">'
      + '<button type="button" class="doc-tab doc-tab--active" role="tab" aria-selected="true" data-tab="content">Conteúdo</button>'
      + '</div>';

    // Corpo da tab
    html += '<div class="doc-tab-body" data-tab-content="content">';
    if (error) {
      html += _buildErrorState();
    } else if (!hasContent) {
      html += '<div class="doc-no-content">'
        + '<svg width="36" height="36" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>'
        + '<p>Documento não processado ainda.<br>Clique em <em>Abrir original</em> para acessar o arquivo.</p>'
        + '</div>';
    } else {
      html += '<div class="doc-content-text">' + Utils.escapeHtml(content).replace(/\n/g, '<br>') + '</div>';
    }
    html += '</div>';

    return html;
  }

  function _bindPanelTabs(container) {
    container.querySelectorAll('.doc-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var tabId = tab.getAttribute('data-tab');
        container.querySelectorAll('.doc-tab').forEach(function (t) {
          t.classList.toggle('doc-tab--active', t.getAttribute('data-tab') === tabId);
          t.setAttribute('aria-selected', t.getAttribute('data-tab') === tabId ? 'true' : 'false');
        });
        container.querySelectorAll('[data-tab-content]').forEach(function (body) {
          body.style.display = body.getAttribute('data-tab-content') === tabId ? '' : 'none';
        });
      });
    });
  }

  function _buildLoadingSkeleton() {
    return '<div class="doc-loading">'
      + '<div class="skeleton skeleton--sm"></div>'
      + '<div class="skeleton skeleton--md"></div>'
      + '<div class="skeleton skeleton--lg"></div>'
      + '<div class="skeleton skeleton--md"></div>'
      + '</div>';
  }

  function _buildErrorState() {
    return '<div class="doc-no-content">'
      + '<svg width="36" height="36" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
      + '<p>Não foi possível carregar o conteúdo do documento.</p>'
      + '</div>';
  }

  function _filenameFromUrl(url) {
    try {
      var parts = url.split('/');
      return decodeURIComponent(parts[parts.length - 1]) || 'Documento';
    } catch (_) { return 'Documento'; }
  }

  function _inferType(url) {
    var ext = (url || '').split('.').pop().toLowerCase();
    return ext || 'doc';
  }

  // ===== API Pública =====
  root.Documents = {
    open:     openDocument,
    openMeta: openDocumentMeta,
  };

})(window);
