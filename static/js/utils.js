/* Cristal 2.0 — Utils: Markdown, tabelas, citações (Etapa 13) */
(function (root) {
  'use strict';

  /* ===== escapeHtml ===== */
  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ===== inlineMarkdown ===== */
  /* Markdown inline: bold, italic, code, links, URLs puras */
  function inlineMarkdown(text) {
    var html = escapeHtml(text);
    // Links markdown: [texto](url)
    html = html.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer" class="ec-inline-link">$1</a>'
    );
    // URLs puras inline (após escapeHtml, https:// está intacto)
    html = html.replace(/(https?:\/\/[^\s&<"]+)/g, function (url) {
      var raw = url.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
      return '<a href="' + raw + '" target="_blank" rel="noopener noreferrer" class="ec-inline-link">' + url + '</a>';
    });
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    html = html.replace(/`(.+?)`/g, '<code>$1</code>');
    return html;
  }

  /* ===== markdownToHtml ===== */
  /* Converte markdown para HTML com suporte a headers, listas ol/ul e bold */
  function markdownToHtml(text, citations) {
    if (!text) return '';

    function processInline(line) {
      var h = escapeHtml(line);
      h = h.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer" class="ec-inline-link">$1</a>');
      h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
      h = h.replace(/`(.+?)`/g, '<code>$1</code>');
      return h;
    }

    var lines = text.split('\n');
    var parts = [];
    var listBuf = [];
    var listType = null;
    var paraBuf = [];

    function flushList() {
      if (!listBuf.length) return;
      parts.push('<' + listType + '>' + listBuf.join('') + '</' + listType + '>');
      listBuf = [];
      listType = null;
    }

    function flushPara() {
      if (!paraBuf.length) return;
      parts.push('<p>' + paraBuf.join('<br>') + '</p>');
      paraBuf = [];
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var trimmed = line.trim();

      if (!trimmed) {
        flushList();
        flushPara();
        continue;
      }

      // ### Header
      if (/^### /.test(trimmed)) {
        flushList(); flushPara();
        parts.push('<h3 class="md-h3">' + processInline(trimmed.slice(4)) + '</h3>');
        continue;
      }

      // ## Header
      if (/^## /.test(trimmed)) {
        flushList(); flushPara();
        parts.push('<h2 class="md-h2">' + processInline(trimmed.slice(3)) + '</h2>');
        continue;
      }

      // Ordered list item
      var olMatch = trimmed.match(/^(\d+)\. (.+)$/);
      if (olMatch) {
        flushPara();
        if (listType !== 'ol') { flushList(); listType = 'ol'; }
        listBuf.push('<li>' + processInline(olMatch[2]) + '</li>');
        continue;
      }

      // Unordered list item
      var ulMatch = trimmed.match(/^[-*] (.+)$/);
      if (ulMatch) {
        flushPara();
        if (listType !== 'ul') { flushList(); listType = 'ul'; }
        listBuf.push('<li>' + processInline(ulMatch[1]) + '</li>');
        continue;
      }

      // Regular paragraph line
      flushList();
      paraBuf.push(processInline(line));
    }

    flushList();
    flushPara();

    var html = parts.join('');

    if (citations && citations.length) {
      html = renderCitationRefs(html, citations);
    }
    return html;
  }

  /* ===== renderCitationRefs ===== */
  /* Substitui [n] no HTML por superscript clicável com âncora para seção de fontes */
  function renderCitationRefs(html, citations) {
    if (!citations || !citations.length) return html;
    return html.replace(/\[(\d+)\]/g, function (match, num) {
      var n = parseInt(num, 10);
      var cite = citations[n - 1];
      if (!cite) return match;
      var title = escapeHtml(cite.title || cite.url || '');
      return (
        '<sup><a href="#cite-' + n + '" class="citation-ref" title="' + title + '">[' + n + ']</a></sup>'
      );
    });
  }

  /* ===== buildCitationsSection ===== */
  /* Renderiza seção "Fontes" numerada a partir de citations[] */
  function buildCitationsSection(citations) {
    if (!citations || !citations.length) return '';
    var html = '<div class="citations-section"><p class="citations-label">Fontes</p><ol class="citations-list">';
    citations.forEach(function (c, i) {
      html += '<li id="cite-' + (i + 1) + '" class="citation-item">';
      if (c.url) {
        html += '<a href="' + escapeHtml(c.url) + '" target="_blank" rel="noopener noreferrer" class="citation-link">'
          + escapeHtml(c.title || c.url) + '</a>';
      } else {
        html += escapeHtml(c.title || '');
      }
      html += '</li>';
    });
    html += '</ol></div>';
    return html;
  }

  /* ===== renderTablesSection ===== */
  /* Renderiza field tables[] da resposta (estruturado: {title, headers[], rows[][]}) */
  /* Adiciona linha de totalização automática para colunas numéricas */
  function renderTablesSection(tables) {
    if (!tables || !tables.length) return '';

    function parseBrNumber(str) {
      if (str == null) return NaN;
      var s = String(str).replace(/[R$\s%]/g, '');
      s = s.replace(/\.(\d{3})/g, '$1'); // remove separador de milhar (ponto antes de 3 dígitos)
      s = s.replace(',', '.');
      return parseFloat(s);
    }

    var html = '';
    tables.forEach(function (tbl) {
      if (!tbl.headers || !tbl.rows || !tbl.rows.length) return;
      html += '<div class="ec-table-wrapper">';
      if (tbl.title) {
        html += '<p class="ec-table-caption">' + escapeHtml(tbl.title) + '</p>';
      }
      html += '<table class="ec-table"><thead><tr>';
      tbl.headers.forEach(function (h) {
        html += '<th>' + escapeHtml(String(h)) + '</th>';
      });
      html += '</tr></thead><tbody>';
      tbl.rows.forEach(function (row) {
        html += '<tr>';
        (Array.isArray(row) ? row : [row]).forEach(function (cell) {
          html += '<td>' + escapeHtml(String(cell == null ? '' : cell)) + '</td>';
        });
        html += '</tr>';
      });
      html += '</tbody>';

      // Detecta colunas numéricas e calcula totais
      var colCount = tbl.headers.length;
      var colSums = new Array(colCount).fill(0);
      var colNumeric = new Array(colCount).fill(0);
      var colHasDecimal = new Array(colCount).fill(false);

      tbl.rows.forEach(function (row) {
        var cells = Array.isArray(row) ? row : [row];
        cells.forEach(function (cell, ci) {
          var v = parseBrNumber(cell);
          if (!isNaN(v)) {
            colSums[ci] += v;
            colNumeric[ci]++;
            if (String(cell).indexOf(',') !== -1 || (String(cell).replace(/[R$\s]/g, '').indexOf('.') !== -1 && !/^\d{1,3}(\.\d{3})+$/.test(String(cell).replace(/[R$\s]/g, '')))) {
              colHasDecimal[ci] = true;
            }
          }
        });
      });

      var threshold = Math.ceil(tbl.rows.length / 2);
      var hasNumericCol = colNumeric.some(function (n) { return n >= threshold; });

      if (hasNumericCol) {
        html += '<tfoot><tr>';
        tbl.headers.forEach(function (_, ci) {
          if (colNumeric[ci] < threshold) {
            html += '<td class="ec-table-total-label">' + (ci === 0 ? '<strong>Total</strong>' : '') + '</td>';
          } else {
            var sum = colSums[ci];
            var display = colHasDecimal[ci]
              ? sum.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
              : sum.toLocaleString('pt-BR');
            html += '<td class="ec-table-total"><strong>' + escapeHtml(display) + '</strong></td>';
          }
        });
        html += '</tr></tfoot>';
      }

      html += '</table></div>';
    });
    return html;
  }

  /* ===== renderMetricsCards ===== */
  /* Renderiza campo metrics[] como cards horizontais de KPI */
  function renderMetricsCards(metrics) {
    if (!metrics || !metrics.length) return '';
    var html = '<div class="metrics-cards">';
    metrics.forEach(function (m) {
      html += '<div class="metric-card">'
        + '<span class="metric-value">' + escapeHtml(String(m.value || '')) + '</span>'
        + '<span class="metric-label">' + escapeHtml(String(m.label || '')) + '</span>'
        + '</div>';
    });
    html += '</div>';
    return html;
  }

  /* ===== Helpers internos para tabelas de conteúdo extraído ===== */

  var RE_MONTHS = /^(JANEIRO|FEVEREIRO|MAR[CÇ]O|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)$/i;
  var RE_FORMATS = /^(PDF|XLSX?|CSV|ODS|DOCX?|ODT|RTF|ZIP|RAR)$/i;
  var RE_YEAR = /^\d{4}$/;
  var RE_HEADER = /^(ano|per[ií]odo|m[eê]s|formato|tipo|arquivo|download)$/i;

  function findTableStart(lines) {
    var yearCount = 0, monthCount = 0;
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i].trim();
      if (RE_YEAR.test(l)) yearCount++;
      else if (RE_MONTHS.test(l)) monthCount++;
    }
    if (yearCount < 2 || monthCount < 2) return -1;

    for (var j = 0; j < lines.length; j++) {
      var line = lines[j].trim();
      if (RE_HEADER.test(line)) {
        var start = j;
        if (start > 0 && /^arquivos?\s+em\s+formato/i.test(lines[start - 1].trim())) start--;
        return start;
      }
      if (RE_YEAR.test(line)) {
        var start2 = j;
        while (start2 > 0) {
          var prev = lines[start2 - 1].trim();
          if (RE_HEADER.test(prev) || /^arquivos?\s+em\s+formato/i.test(prev)) start2--;
          else break;
        }
        return start2;
      }
    }
    return -1;
  }

  function tryBuildTable(lines) {
    var rows = [];
    var currentYear = null, currentMonth = null, currentFormats = [], contextLines = [];

    function flushRow() {
      if (currentYear && currentMonth) {
        rows.push({ ano: currentYear, periodo: currentMonth, formatos: currentFormats.slice() });
      }
      currentFormats = [];
    }

    for (var j = 0; j < lines.length; j++) {
      var line = lines[j].trim();
      if (RE_HEADER.test(line)) continue;
      if (RE_YEAR.test(line)) { flushRow(); currentYear = line; currentMonth = null; continue; }
      if (RE_MONTHS.test(line)) {
        flushRow();
        currentMonth = line.charAt(0).toUpperCase() + line.slice(1).toLowerCase();
        continue;
      }
      if (RE_FORMATS.test(line)) { currentFormats.push(line.toUpperCase()); continue; }
      if (!currentYear) contextLines.push(line);
    }
    flushRow();

    if (rows.length < 2) return null;

    var hasFormats = rows.some(function (r) { return r.formatos.length > 0; });
    var html = '';
    if (contextLines.length > 0) {
      html += '<p class="ec-description">' + escapeHtml(contextLines.join(' ')) + '</p>';
    }

    html += '<div class="ec-table-wrapper"><table class="ec-table"><thead><tr><th>Ano</th><th>Período</th>';
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
          var cls = f === 'PDF' ? 'ec-badge-pdf'
            : (f === 'XLSX' || f === 'XLS') ? 'ec-badge-xlsx'
            : 'ec-badge-other';
          return '<span class="ec-badge ' + cls + '">' + escapeHtml(f) + '</span>';
        }).join(' ');
        html += '<td>' + (badges || '—') + '</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table></div>';
    return html;
  }

  function tryBuildMarkdownTable(lines) {
    var tableLines = [], nonTableLines = [], inTable = false;
    var SEP_ICON = '<svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" style="display:inline;vertical-align:middle;margin-right:4px"><path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>';

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
      if (/^[\s\-:]+$/.test(cells[0])) continue;
      var cellTag = (j === 0) ? 'th' : 'td';
      if (j === 0) html += '<thead>';
      if (j === 1 || (j === 2 && /^[\s\-:]+$/.test((tableLines[1].split('|').filter(function(c){ return c.trim() !== ''; })[0] || '')))) {
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

  /* ===== formatExtractedContent ===== */
  /* Formata conteúdo bruto extraído de página web para exibição rica */
  function formatExtractedContent(raw) {
    if (!raw) return '<p class="ec-empty">Sem conteúdo disponível.</p>';

    var noisePatterns = [
      /^compartilhar\s+p[áa]gina?\s+via/i,
      /@@site-logo/i,
    ];

    var filtered = [];
    raw.split('\n').forEach(function (line) {
      var l = line.trim();
      if (!l) return;
      for (var j = 0; j < noisePatterns.length; j++) {
        if (noisePatterns[j].test(l)) return;
      }
      filtered.push(l);
    });

    if (!filtered.length) return '<p class="ec-empty">Sem conteúdo disponível.</p>';

    var tableStart = findTableStart(filtered);
    var introLines = (tableStart >= 0) ? filtered.slice(0, tableStart) : filtered;
    var dataLines  = (tableStart >= 0) ? filtered.slice(tableStart) : [];

    var htmlParts = [], listItems = [];

    for (var k = 0; k < introLines.length; k++) {
      var text = introLines[k];
      var isUrl = /^https?:\/\/\S+$/i.test(text);

      if (isUrl) {
        if (listItems.length) {
          htmlParts.push('<ul class="ec-list">' + listItems.join('') + '</ul>');
          listItems = [];
        }
        var display = text.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
        if (display.length > 60) display = display.substring(0, 57) + '…';
        htmlParts.push(
          '<a href="' + escapeHtml(text) + '" target="_blank" rel="noopener noreferrer" class="ec-link">'
          + '<svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>'
          + '<span>' + escapeHtml(display) + '</span></a>'
        );
      } else if (k === 0 || (k === 1 && introLines[0].length < 60)) {
        if (listItems.length) {
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

    if (listItems.length) {
      htmlParts.push('<ul class="ec-list">' + listItems.join('') + '</ul>');
    }

    if (dataLines.length) {
      var tableHtml = tryBuildTable(dataLines);
      if (tableHtml) htmlParts.push(tableHtml);
    } else {
      var mdTable = tryBuildMarkdownTable(filtered);
      if (mdTable) return mdTable;
    }

    return htmlParts.join('') || '<p class="ec-empty">Sem conteúdo disponível.</p>';
  }

  /* ===== Export ===== */
  root.Utils = {
    escapeHtml:             escapeHtml,
    inlineMarkdown:         inlineMarkdown,
    markdownToHtml:         markdownToHtml,
    renderCitationRefs:     renderCitationRefs,
    buildCitationsSection:  buildCitationsSection,
    renderTablesSection:    renderTablesSection,
    renderMetricsCards:     renderMetricsCards,
    formatExtractedContent: formatExtractedContent,
  };

})(window);
