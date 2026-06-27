/** Small i18n helper for the browser.
 *
 * The server injects ``window.I18N`` as a nested message object. This module
 * exposes ``t(key, fallback)`` to resolve dot-separated keys safely.
 */
(function () {
  "use strict";

  function t(key, fallback) {
    if (typeof key !== "string") {
      return fallback !== undefined ? fallback : String(key);
    }
    const parts = key.split(".");
    let current = window.I18N;
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

  window.t = t;
})();
