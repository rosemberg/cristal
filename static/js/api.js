/* Cristal 2.0 — API: camada de comunicação (Etapa 14) */
(function (root) {
  'use strict';

  var API = {

    /* POST /api/chat */
    chat: function (message, history, sessionId) {
      return fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message:    message,
          history:    history    || [],
          session_id: sessionId  || undefined,
        }),
      });
    },

    /* GET /api/suggest */
    suggest: function () {
      return fetch('/api/suggest');
    },

    /* GET /api/categories */
    categories: function () {
      return fetch('/api/categories');
    },

    /* GET /api/transparency-map */
    getTransparencyMap: function () {
      return fetch('/api/transparency-map');
    },

    /* GET /api/documents?category=X&doc_type=Y&page=N&size=M */
    getDocuments: function (filters) {
      var params = new URLSearchParams();
      if (filters) {
        if (filters.category) params.set('category', filters.category);
        if (filters.doc_type) params.set('doc_type', filters.doc_type);
        if (filters.page)     params.set('page',     filters.page);
        if (filters.size)     params.set('size',     filters.size);
      }
      var qs = params.toString();
      return fetch('/api/documents' + (qs ? '?' + qs : ''));
    },

    /* GET /api/documents/{url}/content */
    getDocumentContent: function (docUrl) {
      return fetch('/api/documents/' + encodeURIComponent(docUrl) + '/content');
    },

    /* GET /api/sessions */
    listSessions: function () {
      return fetch('/api/sessions');
    },

    /* POST /api/sessions */
    createSession: function (title) {
      return fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title || null }),
      });
    },

    /* GET /api/sessions/:id */
    getSession: function (id) {
      return fetch('/api/sessions/' + id);
    },

    /* GET /api/sessions/:id/messages */
    getSessionMessages: function (id) {
      return fetch('/api/sessions/' + id + '/messages');
    },

    /* POST /api/feedback — fire-and-forget; falha silenciosa */
    sendFeedback: function (queryId, feedback) {
      fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query_id: queryId, feedback: feedback }),
      }).catch(function () { /* intencional: endpoint pode não existir ainda */ });
    },

  };

  root.API = API;

})(window);
