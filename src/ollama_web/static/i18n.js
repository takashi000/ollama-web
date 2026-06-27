/** Small i18n helper for the browser.
 *
 * The server stores the nested message object as JSON in a non-executable
 * meta element. This module parses it and exposes ``t(key, fallback)`` to
 * resolve dot-separated keys safely without requiring inline JavaScript.
 */
(function () {
  "use strict";

  const dataEl = document.querySelector('meta[name="ollama-web-i18n"]');
  let messages = {};

  if (!dataEl) {
    console.error('i18n data element "ollama-web-i18n" was not found');
  } else {
    try {
      const parsed = JSON.parse(dataEl.getAttribute("content") || "");
      if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new TypeError("i18n data must be a JSON object");
      }
      messages = parsed;
    } catch (err) {
      console.error("failed to parse i18n data", err);
    }
  }

  function t(key, fallback) {
    if (typeof key !== "string") {
      return fallback !== undefined ? fallback : String(key);
    }
    const parts = key.split(".");
    let current = messages;
    for (const part of parts) {
      if (current == null || typeof current !== "object" || !(part in current)) {
        return fallback !== undefined ? fallback : key;
      }
      current = current[part];
    }
    if (typeof current === "string") {
      return current;
    }
    return fallback !== undefined ? fallback : key;
  }

  window.I18N = messages;
  window.t = t;
})();
