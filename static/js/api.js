/* Cristal 2.0 — API: camada de comunicação (Etapa 13) */
(function (root) {
  'use strict';

  var API = {

    /* POST /api/chat */
    chat: function (message, history) {
      return fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: message, history: history || [] }),
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
