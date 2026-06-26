"use strict";

window.TOOLS = [
  { name: "web_search", label: "Web検索" },
  { name: "scrape_url", label: "スクレイピング" },
  { name: "search_and_fetch", label: "ファイル検索・取得" },
  { name: "pdf_to_text", label: "PDFテキスト変換" },
];

window.toolLabel = function toolLabel(name) {
  const builtin = window.TOOLS.find((t) => t.name === name);
  if (builtin) return builtin.label;

  const mcpMatch = /^mcp__([^_]+)__(.+)$/.exec(name);
  if (mcpMatch) {
    return `${mcpMatch[2]} (${mcpMatch[1]})`;
  }
  return name;
};
