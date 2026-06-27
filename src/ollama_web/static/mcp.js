"use strict";

(function () {
  const modal = document.getElementById("mcp-modal");
  const openBtn = document.getElementById("mcp-settings-btn");
  const closeBtn = document.getElementById("mcp-modal-close");
  const addBtn = document.getElementById("mcp-add-btn");
  const saveBtn = document.getElementById("mcp-save-btn");
  const serversEl = document.getElementById("mcp-servers");

  let currentConfig = { mcpServers: {} };

  function csrfHeaders(headers = {}) {
    const token = document.cookie.split("; ").find((row) => row.startsWith("ollama_web_csrf="));
    if (!token) return headers;
    return { ...headers, "X-CSRF-Token": decodeURIComponent(token.split("=")[1]) };
  }

  async function csrfFetch(url, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = ["POST", "PUT", "PATCH", "DELETE"].includes(method)
      ? csrfHeaders(options.headers || {})
      : options.headers;
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
    }
    return res;
  }

  function createTextarea(value, placeholder, rows) {
    const el = document.createElement("textarea");
    el.rows = rows || 2;
    el.placeholder = placeholder || "";
    el.value = value || "";
    return el;
  }

  function createInput(value, placeholder, type) {
    const el = document.createElement("input");
    el.type = type || "text";
    el.placeholder = placeholder || "";
    el.value = value || "";
    return el;
  }

  function renderServer(name, server) {
    const card = document.createElement("div");
    card.className = "mcp-server-card";

    const header = document.createElement("div");
    header.className = "mcp-server-header";

    const nameInput = createInput(name, t("mcp.server_name"), "text");
    nameInput.className = "mcp-server-name";

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "mcp-server-delete";
    delBtn.textContent = t("common.delete");
    delBtn.addEventListener("click", () => card.remove());

    header.append(nameInput, delBtn);

    const typeSelect = document.createElement("select");
    typeSelect.className = "mcp-server-type";
    typeSelect.innerHTML = `
      <option value="stdio">${t("mcp.stdio", "stdio")}</option>
      <option value="http">${t("mcp.http_stream", "HTTP Stream")}</option>
    `;
    const isHttp = "url" in server;
    typeSelect.value = isHttp ? "http" : "stdio";

    const fieldsEl = document.createElement("div");
    fieldsEl.className = "mcp-server-fields";

    function wrapField(label, control, required) {
      const wrap = document.createElement("label");
      wrap.className = "mcp-field";
      const span = document.createElement("span");
      span.textContent = `${label} ${required ? t("mcp.required") : t("mcp.optional")}`;
      wrap.append(span, control);
      return wrap;
    }

    function renderFields() {
      fieldsEl.innerHTML = "";
      const type = typeSelect.value;
      if (type === "stdio") {
        const command = createInput(server.command || "", t("mcp.examples.command"), "text");
        command.dataset.key = "command";
        const args = createTextarea(
          (server.args || []).join("\n"),
          t("mcp.examples.args"),
          3
        );
        args.dataset.key = "args";
        const env = createTextarea(
          Object.entries(server.env || {})
            .map(([k, v]) => `${k}=${v}`)
            .join("\n"),
          t("mcp.examples.env"),
          3
        );
        env.dataset.key = "env";
        const cwd = createInput(server.cwd || "", t("mcp.examples.cwd"), "text");
        cwd.dataset.key = "cwd";

        fieldsEl.append(
          wrapField(t("mcp.command"), command, true),
          wrapField(t("mcp.args"), args, false),
          wrapField(t("mcp.env"), env, false),
          wrapField(t("mcp.cwd"), cwd, false)
        );
      } else {
        const url = createInput(server.url || "", t("mcp.examples.url"), "text");
        url.dataset.key = "url";
        const headers = createTextarea(
          Object.entries(server.headers || {})
            .map(([k, v]) => `${k}=${v}`)
            .join("\n"),
          t("mcp.examples.headers"),
          3
        );
        headers.dataset.key = "headers";
        const timeout = createInput(
          server.timeout !== undefined ? String(server.timeout) : "",
          t("mcp.examples.timeout"),
          "number"
        );
        timeout.dataset.key = "timeout";

        fieldsEl.append(
          wrapField(t("mcp.url"), url, true),
          wrapField(t("mcp.headers"), headers, false),
          wrapField(t("mcp.timeout"), timeout, false)
        );
      }
    }

    typeSelect.addEventListener("change", () => {
      server = isHttp ? { url: "" } : { command: "" };
      renderFields();
    });

    card.append(header, typeSelect, fieldsEl);
    renderFields();
    return card;
  }

  function parseServer(card) {
    const name = card.querySelector(".mcp-server-name").value.trim();
    if (!name) return null;

    const type = card.querySelector(".mcp-server-type").value;
    const fields = card.querySelectorAll(".mcp-server-fields [data-key]");
    const server = {};

    for (const field of fields) {
      const key = field.dataset.key;
      const value = field.value.trim();
      if (!value) continue;

      if (type === "stdio") {
        if (key === "command") server.command = value;
        if (key === "args") server.args = value.split("\n").map((s) => s.trim()).filter(Boolean);
        if (key === "env") {
          server.env = {};
          for (const line of value.split("\n")) {
            const idx = line.indexOf("=");
            if (idx > 0) server.env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
          }
        }
        if (key === "cwd") server.cwd = value;
      } else {
        if (key === "url") server.url = value;
        if (key === "headers") {
          server.headers = {};
          for (const line of value.split("\n")) {
            const idx = line.indexOf("=");
            if (idx > 0) server.headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
          }
        }
        if (key === "timeout") server.timeout = parseFloat(value);
      }
    }

    return { name, server };
  }

  async function loadConfig() {
    try {
      const res = await fetch("/api/mcp/servers");
      if (!res.ok) throw new Error(res.statusText);
      currentConfig = await res.json();
    } catch (err) {
      console.error("failed to load MCP config", err);
      currentConfig = { mcpServers: {} };
    }
    render();
  }

  function render() {
    serversEl.innerHTML = "";
    const servers = currentConfig.mcpServers || {};
    for (const [name, server] of Object.entries(servers)) {
      if (server && typeof server === "object") {
        serversEl.appendChild(renderServer(name, server));
      }
    }
  }

  function openModal() {
    loadConfig();
    modal.classList.add("open");
  }

  function closeModal() {
    modal.classList.remove("open");
  }

  openBtn.addEventListener("click", openModal);
  closeBtn.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  addBtn.addEventListener("click", () => {
    const card = renderServer("new_server", { command: "" });
    serversEl.appendChild(card);
  });

  saveBtn.addEventListener("click", async () => {
    const cards = serversEl.querySelectorAll(".mcp-server-card");
    const servers = {};
    for (const card of cards) {
      const parsed = parseServer(card);
      if (parsed) servers[parsed.name] = parsed.server;
    }

    const payload = { mcpServers: servers };
    try {
      const res = await csrfFetch("/api/mcp/servers", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const errorText = (data.error || []).join(" / ") || res.statusText;
        alert(t("mcp_messages.save_failed", "Failed to save: {error}").replace("{error}", errorText));
        return;
      }
      currentConfig = await res.json();
      alert(t("mcp_messages.saved"));
      closeModal();
    } catch (err) {
      console.error("failed to save MCP config", err);
      alert(t("mcp_messages.save_error"));
    }
  });

  loadConfig();
})();