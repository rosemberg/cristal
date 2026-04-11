/* Cristal 2.0 — Map: mapa de transparência (Etapa 14) */
/* Depende de: api.js (window.API), utils.js (window.Utils) */
(function (root) {
  'use strict';

  var _mapData   = null;
  var _rendered  = false;
  var _filterVal = '';

  // ===== Carregar e renderizar o mapa =====
  async function load() {
    var container = document.getElementById('map-view');
    if (!container) return;

    container.innerHTML = _buildSkeleton();

    try {
      var res = await API.getTransparencyMap();
      if (!res.ok) throw new Error('HTTP ' + res.status);
      _mapData = await res.json();
      _rendered = true;
      _render(_mapData);
    } catch (_) {
      container.innerHTML = '<div class="map-error">'
        + '<p>Não foi possível carregar o mapa de transparência.<br>'
        + 'Verifique a conexão ou tente novamente mais tarde.</p>'
        + '<button type="button" class="map-retry-btn" onclick="Map.load()">Tentar novamente</button>'
        + '</div>';
    }
  }

  function _render(data) {
    var container = document.getElementById('map-view');
    if (!container) return;

    var categories = (data && data.categories) || [];
    var totals     = (data && data.totals)     || {};

    // Filtrar
    var query = _filterVal.trim().toLowerCase();
    var filtered = query
      ? categories.filter(function (c) { return c.category.toLowerCase().includes(query); })
      : categories;

    // Cabeçalho com estatísticas
    var html = '<div class="map-header">'
      + '<div class="map-header-title">'
      + '<svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"/></svg>'
      + '<h2>Mapa de Transparência</h2>'
      + '</div>'
      + _buildStats(totals)
      + '</div>';

    // Filtro
    html += '<div class="map-filter-wrap">'
      + '<div class="map-filter-inner">'
      + '<svg class="map-filter-icon" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>'
      + '<input id="map-filter-input" type="search" class="map-filter-input" placeholder="Filtrar categorias..." value="' + Utils.escapeHtml(query) + '" aria-label="Filtrar categorias">'
      + '</div>'
      + '</div>';

    // Lista de categorias
    html += '<div class="map-categories">';

    if (filtered.length === 0) {
      html += '<div class="map-empty"><p>Nenhuma categoria encontrada para "' + Utils.escapeHtml(query) + '".</p></div>';
    } else {
      filtered.forEach(function (cat) {
        html += _buildCategoryItem(cat);
      });
    }

    html += '</div>';

    // Rodapé
    html += '<div class="map-footer">'
      + '<svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
      + '<span>Clique em qualquer categoria para iniciar uma conversa</span>'
      + '</div>';

    container.innerHTML = html;

    // Bind events
    var filterInput = container.querySelector('#map-filter-input');
    if (filterInput) {
      filterInput.addEventListener('input', function () {
        _filterVal = filterInput.value;
        _render(_mapData);
      });
    }

    container.querySelectorAll('.map-cat-item').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var catName = btn.getAttribute('data-category');
        if (catName) _openCategoryChat(catName);
      });
    });
  }

  function _buildStats(totals) {
    var pages = totals.total_pages || 0;
    var docs  = totals.total_documents || 0;
    return '<div class="map-stats">'
      + '<span class="map-stat"><strong>' + _fmt(pages) + '</strong> páginas</span>'
      + '<span class="map-stat-sep">·</span>'
      + '<span class="map-stat"><strong>' + _fmt(docs) + '</strong> documentos</span>'
      + '</div>';
  }

  function _buildCategoryItem(cat) {
    var name  = cat.category || '';
    var count = cat.page_count || 0;
    return '<button type="button" class="map-cat-item" data-category="' + Utils.escapeHtml(name) + '" aria-label="Ver categoria: ' + Utils.escapeHtml(name) + '">'
      + '<div class="map-cat-icon" aria-hidden="true">'
      + '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>'
      + '</div>'
      + '<div class="map-cat-body">'
      + '<span class="map-cat-name">' + Utils.escapeHtml(name) + '</span>'
      + '<span class="map-cat-count">' + _fmt(count) + ' ' + (count === 1 ? 'página' : 'páginas') + '</span>'
      + '</div>'
      + '<svg class="map-cat-arrow" width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>'
      + '</button>';
  }

  function _buildSkeleton() {
    var s = '<div class="map-skeleton">';
    for (var i = 0; i < 6; i++) {
      s += '<div class="skeleton skeleton--cat"></div>';
    }
    return s + '</div>';
  }

  function _fmt(n) {
    return Number(n).toLocaleString('pt-BR');
  }

  function _openCategoryChat(categoryName) {
    // Navega para chat e pré-preenche pergunta
    if (root.App && root.App.navigate) root.App.navigate('chat');
    var inputField = document.getElementById('input-field');
    if (inputField) {
      inputField.value = 'O que posso encontrar em ' + categoryName + '?';
      inputField.dispatchEvent(new Event('input'));
      inputField.focus();
    }
  }

  // ===== API Pública =====
  root.Map = {
    load: load,
  };

})(window);
