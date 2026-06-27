"use strict";

window.TOOLS = [
  { name: "web_search", i18n_key: "tools.web_search" },
  { name: "scrape_url", i18n_key: "tools.scrape_url" },
  { name: "search_and_fetch", i18n_key: "tools.search_and_fetch" },
  { name: "pdf_to_text", i18n_key: "tools.pdf_to_text" },
];

window.toolLabel = function toolLabel(name) {
  const builtin = window.TOOLS.find((t) => t.name === name);
  if (builtin) {
    return typeof window.t === "function" ? window.t(builtin.i18n_key, builtin.name) : builtin.name;
  }

  const mcpMatch = /^mcp__([^_]+)__(.+)$/.exec(name);
  if (mcpMatch) {
    return `${mcpMatch[2]} (${mcpMatch[1]})`;
  }
  return name;
};
