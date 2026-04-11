/* Cristal 2.0 — Analytics: dashboard admin (Etapa 15) */
/* Depende de: api.js (window.API), utils.js (window.Utils) */
(function (root) {
  'use strict';

  var _data    = null;
  var _days    = 30;
  var _loading = false;

  // ===== Carregar dados =====
  async function load(days) {
    if (_loading) return;
    _loading = true;
    _days = days || 30;

    var container = document.getElementById('analytics-view');
    if (!container) { _loading = false; return; }

    container.innerHTML = _buildSkeleton();

    try {
      var res = await API.getAnalytics(_days);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      _data = await res.json();
      _render(_data);
    } catch (_err) {
      container.innerHTML = '<div class="analytics-error">'
        + '<svg width="32" height="32" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
        + '<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126z"/>'
        + '</svg>'
        + '<p>Não foi possível carregar as métricas.<br>Verifique a conexão e tente novamente.</p>'
        + '<button type="button" class="analytics-retry-btn" onclick="Analytics.load()">Tentar novamente</button>'
        + '</div>';
    } finally {
      _loading = false;
    }
  }

  // ===== Renderizar dashboard =====
  function _render(data) {
    var container = document.getElementById('analytics-view');
    if (!container) return;

    var m     = data.metrics    || {};
    var stats = data.daily_stats || [];
    var days  = data.days        || _days;

    var html = '<div class="analytics-dashboard">';

    // Cabeçalho
    html += '<div class="analytics-header">'
      + '<div class="analytics-header-title">'
      + '<svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">'
      + '<path stroke-linecap="round" stroke-linejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>'
      + '</svg>'
      + '<h2>Dashboard Admin</h2>'
      + '</div>'
      + '<div class="analytics-period-selector" role="group" aria-label="Período de análise">'
      + _periodBtn(7,  days)
      + _periodBtn(30, days)
      + _periodBtn(90, days)
      + '</div>'
      + '</div>';

    // Cards KPI
    html += '<div class="analytics-kpi-grid">'
      + _kpiCard(
          'Consultas',
          _fmt(m.total_queries || 0),
          'Total de perguntas no período',
          '<path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>',
          'kpi--primary'
        )
      + _kpiCard(
          'Tempo médio',
          _fmtMs(m.avg_response_time_ms || 0),
          'Tempo médio de resposta',
          '<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>',
          'kpi--neutral'
        )
      + _kpiCard(
          'Satisfação',
          _fmtPct(m.satisfaction_rate || 0),
          _fmt(m.positive_feedback || 0) + ' positivos · ' + _fmt(m.negative_feedback || 0) + ' negativos',
          '<path stroke-linecap="round" stroke-linejoin="round" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"/>',
          _satisfactionClass(m.satisfaction_rate)
        )
      + _kpiCard(
          'Feedbacks',
          _fmt((m.positive_feedback || 0) + (m.negative_feedback || 0)),
          'Total de avaliações recebidas',
          '<path stroke-linecap="round" stroke-linejoin="round" d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"/>',
          'kpi--neutral'
        )
      + '</div>';

    // Tabela de stats diárias
    html += '<div class="analytics-table-wrap">'
      + '<div class="analytics-table-header">'
      + '<svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">'
      + '<path stroke-linecap="round" stroke-linejoin="round" d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"/>'
      + '</svg>'
      + '<span>Consultas por dia — últimos ' + days + ' dias</span>'
      + '</div>'
      + _buildTable(stats)
      + '</div>';

    html += '</div>'; // /analytics-dashboard

    container.innerHTML = html;

    // Bind: botões de período
    container.querySelectorAll('.analytics-period-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var d = parseInt(btn.getAttribute('data-days'), 10);
        if (!isNaN(d)) load(d);
      });
    });
  }

  // ===== Helpers de UI =====

  function _periodBtn(d, activeDays) {
    var active = d === activeDays ? ' analytics-period-btn--active' : '';
    return '<button type="button" class="analytics-period-btn' + active + '" data-days="' + d + '" aria-pressed="' + (d === activeDays) + '">'
      + d + 'd'
      + '</button>';
  }

  function _kpiCard(label, value, sub, iconPath, modifier) {
    return '<div class="analytics-kpi-card ' + modifier + '" role="figure" aria-label="' + Utils.escapeHtml(label) + ': ' + Utils.escapeHtml(value) + '">'
      + '<div class="kpi-icon" aria-hidden="true">'
      + '<svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">' + iconPath + '</svg>'
      + '</div>'
      + '<div class="kpi-body">'
      + '<span class="kpi-value">' + Utils.escapeHtml(value) + '</span>'
      + '<span class="kpi-label">' + Utils.escapeHtml(label) + '</span>'
      + '<span class="kpi-sub">' + Utils.escapeHtml(sub) + '</span>'
      + '</div>'
      + '</div>';
  }

  function _buildTable(stats) {
    if (!stats || stats.length === 0) {
      return '<div class="analytics-empty"><p>Sem dados para o período selecionado.</p></div>';
    }

    var maxQueries = Math.max.apply(null, stats.map(function (s) { return s.query_count || 0; }));

    var rows = stats.map(function (s) {
      var pct = maxQueries > 0 ? Math.round((s.query_count / maxQueries) * 100) : 0;
      return '<tr>'
        + '<td class="atd-date">' + Utils.escapeHtml(_fmtDate(s.date)) + '</td>'
        + '<td class="atd-bar">'
        + '<div class="atd-bar-wrap" role="img" aria-label="' + s.query_count + ' consultas">'
        + '<div class="atd-bar-fill" style="width:' + pct + '%"></div>'
        + '</div>'
        + '</td>'
        + '<td class="atd-count">' + _fmt(s.query_count) + '</td>'
        + '<td class="atd-ms">' + _fmtMs(s.avg_response_time_ms) + '</td>'
        + '</tr>';
    }).join('');

    return '<div class="analytics-table-scroll">'
      + '<table class="analytics-table" aria-label="Estatísticas diárias">'
      + '<thead><tr>'
      + '<th scope="col">Data</th>'
      + '<th scope="col">Volume</th>'
      + '<th scope="col">Consultas</th>'
      + '<th scope="col">Tempo médio</th>'
      + '</tr></thead>'
      + '<tbody>' + rows + '</tbody>'
      + '</table>'
      + '</div>';
  }

  function _buildSkeleton() {
    var cards = '';
    for (var i = 0; i < 4; i++) {
      cards += '<div class="skeleton analytics-skeleton-card"></div>';
    }
    return '<div class="analytics-skeleton">'
      + '<div class="analytics-skeleton-header"><div class="skeleton analytics-skeleton-title"></div></div>'
      + '<div class="analytics-skeleton-kpi">' + cards + '</div>'
      + '<div class="skeleton analytics-skeleton-table"></div>'
      + '</div>';
  }

  // ===== Formatadores =====

  function _fmt(n) {
    return Number(n).toLocaleString('pt-BR');
  }

  function _fmtMs(ms) {
    var n = parseFloat(ms) || 0;
    return n >= 1000 ? (n / 1000).toFixed(1) + ' s' : Math.round(n) + ' ms';
  }

  function _fmtPct(rate) {
    return Math.round((parseFloat(rate) || 0) * 100) + '%';
  }

  function _fmtDate(dateStr) {
    if (!dateStr) return '';
    var parts = dateStr.split('-');
    if (parts.length !== 3) return dateStr;
    return parts[2] + '/' + parts[1] + '/' + parts[0];
  }

  function _satisfactionClass(rate) {
    var r = parseFloat(rate) || 0;
    if (r >= 0.75) return 'kpi--success';
    if (r >= 0.5)  return 'kpi--warning';
    return 'kpi--danger';
  }

  // ===== API Pública =====
  root.Analytics = {
    load: load,
  };

})(window);
