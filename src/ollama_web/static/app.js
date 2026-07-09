"use strict";

const chatEl = document.getElementById("chat");
const formEl = document.getElementById("input-form");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const modelSelect = document.getElementById("model-select");
const thinkToggle = document.getElementById("think-toggle");
const clearBtn = document.getElementById("clear-btn");
const cancelBtn = document.getElementById("cancel-btn");
const fileInput = document.getElementById("file-input");
const attachmentsEl = document.getElementById("attachments");
const sessionListEl = document.getElementById("session-list");
const newSessionBtn = document.getElementById("new-session-btn");
const menuBtn = document.getElementById("menu-btn");
const sidebar = document.getElementById("sidebar");
const sidebarBackdrop = document.getElementById("sidebar-backdrop");
const TOOLS = window.TOOLS || [];

let currentSessionId = null;
let currentSession = null;
let pendingFiles = [];
let abortController = null;
let currentCapabilities = new Set();
let stickToBottom = true;

function updateThinkToggleState() {
  if (!thinkToggle) return;
  if (currentCapabilities.has("thinking")) {
    thinkToggle.disabled = false;
    thinkToggle.parentElement.classList.remove("disabled");
  } else {
    thinkToggle.disabled = true;
    thinkToggle.checked = false;
    thinkToggle.parentElement.classList.add("disabled");
  }
}

async function loadModelCapabilities() {
  const model = modelSelect.value;
  if (!model) return;
  try {
    const res = await fetch(`/api/models/${encodeURIComponent(model)}/capabilities`);
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    currentCapabilities = new Set((data.capabilities || []).map((c) => c.toLowerCase()));
  } catch (err) {
    console.error("failed to load model capabilities", err);
    currentCapabilities = new Set();
  }
  updateThinkToggleState();
}

function getCookie(name) {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return "";
}

function csrfHeaders(headers = {}) {
  const token = getCookie("ollama_web_csrf");
  return token ? { ...headers, "X-CSRF-Token": token } : headers;
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

function simpleMarkdownToHtml(text) {
  const el = document.createElement("div");
  el.setAttribute("style", "white-space:pre-wrap");
  el.textContent = text;
  return el.outerHTML;
}

function protectMath(text) {
  const placeholders = [];
  const store = (display, latex) => {
    const id = `MATH_PLACEHOLDER_${placeholders.length}_`;
    placeholders.push({ id, display, latex });
    return id;
  };
  const processed = text
    .replace(/(?<!\$)\$\$([\s\S]+?)\$\$(?!\$)/g, (_, latex) => store(true, latex))
    .replace(/(?<!\$)\$((?:\\.|[^\\\n$])+?)\$(?!\$)/g, (_, latex) => store(false, latex))
    .replace(/(?<!\S)\\\[([\s\S]+?)\\\](?!\S)/g, (_, latex) => store(true, latex))
    .replace(/(?<!\S)\\\(((?:\\.|[^\\\n])+?)\\\)(?!\S)/g, (_, latex) => store(false, latex));
  return { text: processed, placeholders };
}

function escapeHtmlMath(latex) {
  return latex
    .replace(/&/g, String.fromCharCode(38, 97, 109, 112, 59))
    .replace(/</g, String.fromCharCode(38, 108, 116, 59))
    .replace(/>/g, String.fromCharCode(38, 103, 116, 59));
}

function restoreMath(html, placeholders) {
  let out = html;
  for (const { id, display, latex } of placeholders) {
    const tag = display ? "div" : "span";
    const cls = display ? "math-block" : "math-inline";
    out = out.split(id).join(`<${tag} class="${cls}">${escapeHtmlMath(latex)}</${tag}>`);
  }
  return out;
}

function renderMarkdown(text) {
  if (typeof marked !== "undefined" && marked.parse) {
    try {
      const { text: protectedText, placeholders } = protectMath(text);
      const html = marked.parse(protectedText);
      if (typeof html === "string") {
        const restored = restoreMath(html, placeholders);
        return typeof DOMPurify !== "undefined" && DOMPurify.sanitize
          ? DOMPurify.sanitize(restored)
          : simpleMarkdownToHtml(text);
      }
    } catch {
      // fall through
    }
  }
  return simpleMarkdownToHtml(text);
}

function renderMathIn(el) {
  if (typeof katex === "undefined" || !katex.render) return;
  el.querySelectorAll(".math-inline").forEach((node) => {
    try {
      katex.render(node.textContent, node, { throwOnError: false, displayMode: false });
    } catch {
      /* ignore */
    }
  });
  el.querySelectorAll(".math-block").forEach((node) => {
    try {
      katex.render(node.textContent, node, { throwOnError: false, displayMode: true });
    } catch {
      /* ignore */
    }
  });
}

function highlightIn(el) {
  if (typeof hljs !== "undefined" && hljs.highlightElement) {
    el.querySelectorAll("pre code").forEach((block) => {
      try {
        hljs.highlightElement(block);
      } catch {
        /* ignore */
      }
    });
  }
}

function addMessage(role, rawText, showCopy = false) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.dataset.rawText = rawText || "";
  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role === "user" ? t("roles.user") : t("roles.assistant");
  const body = document.createElement("div");
  body.className = "body";
  div.append(roleEl, body);
  let extras = null;
  if (role === "assistant") {
    extras = document.createElement("div");
    extras.className = "extras";
    div.appendChild(extras);
  }
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "copy-btn";
  copyBtn.title = t("common.copy", "Copy");
  copyBtn.setAttribute("aria-label", t("common.copy", "Copy"));
  copyBtn.textContent = "📋";
  copyBtn.hidden = !showCopy;
  copyBtn.addEventListener("click", () => {
    const raw = div.dataset.rawText || "";
    navigator.clipboard.writeText(raw).then(() => {
      copyBtn.textContent = "✓";
      setTimeout(() => {
        copyBtn.textContent = "📋";
      }, 1200);
    });
  });
  div.appendChild(copyBtn);
  chatEl.appendChild(div);
  if (stickToBottom) {
    chatEl.scrollTop = chatEl.scrollHeight;
  }
  return { body, extras, div, copyBtn };
}

function addToolBubble(parentExtras, name, args) {
  const det = document.createElement("details");
  det.className = "tool";
  const label = typeof window.toolLabel === "function" ? window.toolLabel(name) : name;
  const sum = document.createElement("summary");
  sum.textContent = t("status.executing_tool", "Executing {label}…").replace("{label}", label);
  det.appendChild(sum);
  if (args && Object.keys(args).length) {
    const a = document.createElement("div");
    a.className = "args";
    a.textContent = JSON.stringify(args, null, 2);
    det.appendChild(a);
  }
  const result = document.createElement("div");
  result.className = "result";
  det.appendChild(result);
  parentExtras.appendChild(det);
  if (stickToBottom) {
    chatEl.scrollTop = chatEl.scrollHeight;
  }
  return { det, sum, result };
}

function addThinkingBubble(parentExtras) {
  const det = document.createElement("details");
  det.className = "thinking";
  const sum = document.createElement("summary");
  sum.textContent = t("status.thinking");
  const body = document.createElement("div");
  det.append(sum, body);
  parentExtras.appendChild(det);
  if (stickToBottom) {
    chatEl.scrollTop = chatEl.scrollHeight;
  }
  return body;
}

function parseSseEvent(chunk) {
  const events = [];
  const lines = chunk.split("\n");
  let dataBuffer = "";

  function flush() {
    if (dataBuffer !== "") {
      events.push(dataBuffer);
      dataBuffer = "";
    }
  }

  for (const line of lines) {
    if (line === "") {
      flush();
    } else if (line.startsWith("data: ")) {
      const payload = line.slice(6);
      dataBuffer = dataBuffer === "" ? payload : dataBuffer + "\n" + payload;
    }
    // Lines starting with ':' are comments (e.g. keepalive). All other field
    // lines are ignored because we only care about 'data:' payloads.
  }
  flush();
  return events;
}

async function loadSessions() {
  try {
    const res = await fetch("/api/sessions");
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    renderSessionList(data.sessions || []);
  } catch (err) {
    console.error("failed to load sessions", err);
    renderSessionList([]);
  }
}

function renderSessionList(sessions) {
  sessionListEl.innerHTML = "";
  if (!sessions.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = t("chat.no_sessions");
    sessionListEl.appendChild(li);
    return;
  }
  for (const s of sessions) {
    const li = document.createElement("li");
    li.dataset.id = s.id;
    if (s.id === currentSessionId) li.classList.add("active");
    const title = document.createElement("span");
    title.className = "title";
    title.textContent = s.title || s.id;
    const del = document.createElement("button");
    del.className = "delete";
    del.textContent = t("common.delete");
    del.title = t("chat.delete_session");
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id);
    });
    li.append(title, del);
    li.addEventListener("click", () => selectSession(s.id));
    sessionListEl.appendChild(li);
  }
}

async function createSession() {
  try {
    const res = await csrfFetch("/api/sessions", { method: "POST" });
    if (!res.ok) throw new Error(res.statusText);
    const session = await res.json();
    await loadSessions();
    await selectSession(session.id);
  } catch (err) {
    console.error("failed to create session", err);
    alert(t("chat.create_failed"));
  }
}

async function deleteSession(id) {
  if (!confirm(t("chat.delete_confirm"))) return;
  try {
    const res = await csrfFetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(res.statusText);
    if (currentSessionId === id) {
      currentSessionId = null;
      currentSession = null;
      chatEl.innerHTML = "";
      pendingFiles = [];
      renderAttachments();
      updateTokenGauge(0, 0);
    }
    await loadSessions();
  } catch (err) {
    console.error("failed to delete session", err);
    alert(t("chat.delete_failed"));
  }
}

async function selectSession(id) {
  try {
    const res = await fetch(`/api/sessions/${id}`);
    if (!res.ok) throw new Error(res.statusText);
    const session = await res.json();
    currentSessionId = session.id;
    currentSession = session;
    stickToBottom = true;
    renderSession();
    renderSessionList((await (await fetch("/api/sessions")).json()).sessions || []);
  } catch (err) {
    console.error("failed to select session", err);
  }
}

function escapeHtml(text) {
  return text
    .replace(/&/g, String.fromCharCode(38, 97, 109, 112, 59))
    .replace(/</g, String.fromCharCode(38, 108, 116, 59))
    .replace(/>/g, String.fromCharCode(38, 103, 116, 59))
    .replace(/"/g, String.fromCharCode(38, 113, 117, 111, 116, 59))
    .replace(/'/g, "&#039;");
}

function _renderAssistantContent(body, content) {
  const safeContent = content || "";
  if (!safeContent.trim()) {
    body.textContent = t("chat.no_response");
    body.style.color = "var(--muted)";
    body.style.fontStyle = "italic";
    return;
  }
  body.style.color = "";
  body.style.fontStyle = "";
  if (safeContent.startsWith("[ERROR]")) {
    const lines = safeContent.slice(7).split("\n").filter((l) => l.trim());
    body.innerHTML = "";
    for (const line of lines) {
      const span = document.createElement("span");
      span.style.color = "#f85149";
      span.textContent = line;
      body.appendChild(span);
      body.appendChild(document.createElement("br"));
    }
  } else {
    body.innerHTML = renderMarkdown(safeContent);
    highlightIn(body);
    renderMathIn(body);
  }
}

function renderSession() {
  chatEl.innerHTML = "";
  if (!currentSession) {
    updateTokenGauge(0, 0);
    return;
  }
  const msgs = currentSession.messages || [];
  for (const m of msgs) {
    if (m.role === "user") {
      const { body, div } = addMessage("user", m.content || "", true);
      const content = m.content || "";
      try {
        body.innerHTML = renderMarkdown(content);
        highlightIn(body);
        renderMathIn(body);
      } catch (renderErr) {
        console.error("failed to render user message", renderErr);
        body.textContent = content;
      }
      if (m.attachments && m.attachments.length) {
        const info = document.createElement("div");
        info.style.fontSize = "12px";
        info.style.color = "var(--muted)";
        info.style.marginTop = "6px";
        const names = m.attachments
          .map((fid) => {
            const f = (currentSession.files || []).find((x) => x.id === fid);
            return f ? f.name : fid;
          })
          .join(", ");
        info.textContent = t("chat.attachment_label", "Attached: {names}").replace("{names}", names);
        body.appendChild(info);
        const filesPart = t("chat.attachment_files", "Attached files: {names}").replace("{names}", names);
        div.dataset.rawText = (m.content || "") + "\n\n" + filesPart;
      }
    } else if (m.role === "assistant") {
      const { body } = addMessage("assistant", m.content || "", true);
      try {
        _renderAssistantContent(body, m.content);
      } catch (renderErr) {
        console.error("failed to render assistant message", renderErr);
        body.textContent = m.content || "";
      }
    }
  }
  updateTokenGaugeFromSession();
}

function renderAttachments() {
  attachmentsEl.innerHTML = "";
  for (const f of pendingFiles) {
    const chip = document.createElement("span");
    chip.className = "attachment-chip";
    chip.textContent = f.name;
    const remove = document.createElement("span");
    remove.className = "remove";
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      pendingFiles = pendingFiles.filter((x) => x !== f);
      renderAttachments();
    });
    chip.appendChild(remove);
    attachmentsEl.appendChild(chip);
  }
}

async function uploadFiles(files) {
  if (!currentSessionId) {
    await createSession();
  }
  if (!currentSessionId) return;

  const form = new FormData();
  for (const file of files) {
    form.append("files", file);
  }
  try {
    const res = await csrfFetch(`/api/sessions/${currentSessionId}/files`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    for (const f of data.files || []) {
      if (f.error) {
        alert(`${f.name}: ${f.error}`);
        continue;
      }
      pendingFiles.push({ id: f.id, name: f.name });
    }
    renderAttachments();
    await selectSession(currentSessionId);
  } catch (err) {
    console.error("failed to upload files", err);
    alert(t("chat.upload_failed"));
  }
}

fileInput.addEventListener("change", () => {
  if (fileInput.files && fileInput.files.length) {
    uploadFiles(fileInput.files);
    fileInput.value = "";
  }
});

newSessionBtn.addEventListener("click", createSession);

chatEl.addEventListener("scroll", () => {
  const threshold = 50;
  stickToBottom = chatEl.scrollTop + chatEl.clientHeight >= chatEl.scrollHeight - threshold;
});

menuBtn.addEventListener("click", () => {
  sidebar.classList.add("open");
  sidebarBackdrop.classList.add("active");
});

sidebarBackdrop.addEventListener("click", () => {
  sidebar.classList.remove("open");
  sidebarBackdrop.classList.remove("active");
});

sessionListEl.addEventListener("click", (e) => {
  const li = e.target.closest("li");
  if (li && li.dataset.id && !e.target.closest(".delete")) {
    sidebar.classList.remove("open");
    sidebarBackdrop.classList.remove("active");
  }
});

modelSelect.addEventListener("change", loadModelCapabilities);

async function send() {
  const text = inputEl.value.trim();
  if (!text && !pendingFiles.length) return;

  if (!currentSessionId) {
    await createSession();
  }
  if (!currentSessionId) return;

  if (text) inputEl.value = "";
  sendBtn.disabled = true;
  cancelBtn.disabled = false;
  abortController = new AbortController();
  stickToBottom = true;

  const fileIds = pendingFiles.map((f) => f.id);
  let displayContent = text;
  let userRawText = text;
  if (pendingFiles.length) {
    const names = pendingFiles.map((f) => f.name).join(", ");
    const filesPart = t("chat.attachment_files", "Attached files: {names}").replace("{names}", names);
    displayContent += (text ? "\n\n" : "") + filesPart;
    userRawText += (text ? "\n\n" : "") + filesPart;
  }
  const { body: userBody, div: userDiv, copyBtn: userCopyBtn } = addMessage("user", userRawText);
  userBody.innerHTML = renderMarkdown(displayContent);
  highlightIn(userBody);

  pendingFiles = [];
  renderAttachments();

  const { body: assistantBody, extras: assistantExtras, div: assistantDiv, copyBtn: assistantCopyBtn } = addMessage("assistant");
  assistantBody.textContent = "…";
  let assistantText = "";
  let thinkingBody = null;
  const pendingTools = [];
  let gotError = false;
  let tokenCount = null;
  let numCtx = 0;

  const model = modelSelect.value;
  const think = thinkToggle.checked;

  const requestBody = JSON.stringify({
    model,
    messages: text ? [{ role: "user", content: text }] : [],
    think,
    session_id: currentSessionId,
    file_ids: fileIds,
  });

  let res;
  try {
    res = await csrfFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: requestBody,
      signal: abortController.signal,
    });
  } catch (err) {
    assistantBody.textContent =
      err.name === "AbortError"
        ? t("chat.stopped")
        : t("chat.error_prefix", "Error: {message}").replace("{message}", err.message);
    assistantDiv.dataset.rawText = assistantBody.textContent;
    if (assistantCopyBtn) assistantCopyBtn.hidden = false;
    abortController = null;
    cancelBtn.disabled = true;
    sendBtn.disabled = false;
    inputEl.focus();
    return;
  }

  if (!res.ok || !res.body) {
    assistantBody.textContent = t("chat.error_prefix", "Error: {message}").replace("{message}", res.status);
    assistantDiv.dataset.rawText = assistantBody.textContent;
    if (assistantCopyBtn) assistantCopyBtn.hidden = false;
    abortController = null;
    cancelBtn.disabled = true;
    sendBtn.disabled = false;
    inputEl.focus();
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  let placeholderCleared = false;
  let streamRafId = null;

  function flushStreamFrame() {
    streamRafId = null;
    if (!placeholderCleared) {
      assistantBody.innerHTML = "";
      placeholderCleared = true;
    }
    // Remove the "waiting" placeholder once actual content starts arriving.
    const waiting = assistantBody.querySelector(".waiting-msg");
    if (waiting) {
      waiting.remove();
    }
    // During the stream we render raw text only. Full markdown, syntax
    // highlighting and math rendering are deferred to the final renderSession()
    // call so mobile WebKit is not overwhelmed by rebuilding the DOM for
    // every single token.
    assistantBody.textContent = assistantText;
    assistantBody.style.whiteSpace = "pre-wrap";
    if (stickToBottom) {
      chatEl.scrollTop = chatEl.scrollHeight;
    }
  }

  function scheduleStreamUpdate() {
    if (streamRafId === null) {
      streamRafId = requestAnimationFrame(flushStreamFrame);
    }
  }

  function appendDelta(content) {
    assistantText += content;
    scheduleStreamUpdate();
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const endIndex = buffer.lastIndexOf("\n\n");
      if (endIndex === -1) continue;
      const chunk = buffer.slice(0, endIndex);
      buffer = buffer.slice(endIndex + 2);

      for (const payload of parseSseEvent(chunk)) {
        if (!payload.trim()) continue;
        let ev;
        try {
          ev = JSON.parse(payload);
        } catch (e) {
          console.warn("failed to parse SSE payload", payload, e);
          continue;
        }

        if (ev.type === "tool_start") {
          const bubble = addToolBubble(assistantExtras, ev.name, ev.arguments);
          pendingTools.push(bubble);
        } else if (ev.type === "tool_end") {
          const b = pendingTools.shift();
          if (b) {
            b.sum.textContent = b.sum.textContent.replace(
              t("status.running", "Running…"),
              t("status.done")
            );
            b.result.textContent = (ev.result || "").slice(0, 2000);
            if (stickToBottom) {
              chatEl.scrollTop = chatEl.scrollHeight;
            }
          }
          // Show a placeholder while waiting for the next ollama response so
          // the UI does not look frozen between tool execution and deltas.
          if (!placeholderCleared) {
            assistantBody.innerHTML = "";
            placeholderCleared = true;
          }
          if (assistantBody.querySelector(".waiting-msg") === null) {
            const waiting = document.createElement("span");
            waiting.className = "waiting-msg";
            waiting.style.color = "var(--muted)";
            waiting.style.fontStyle = "italic";
            waiting.textContent = t("chat.generating");
            assistantBody.appendChild(waiting);
          }
        } else if (ev.type === "status") {
          if (!placeholderCleared) {
            assistantBody.innerHTML = "";
            placeholderCleared = true;
          }
          if (assistantBody.querySelector(".waiting-msg") === null) {
            const waiting = document.createElement("span");
            waiting.className = "waiting-msg";
            waiting.style.color = "var(--muted)";
            waiting.style.fontStyle = "italic";
            waiting.textContent = ev.message || t("chat.processing");
            assistantBody.appendChild(waiting);
          }
        } else if (ev.type === "thinking") {
          if (!thinkingBody) thinkingBody = addThinkingBubble(assistantExtras);
          thinkingBody.textContent += ev.content;
          scheduleStreamUpdate();
        } else if (ev.type === "delta") {
          appendDelta(ev.content);
        } else if (ev.type === "error") {
          gotError = true;
          if (!placeholderCleared) {
            assistantBody.innerHTML = "";
            placeholderCleared = true;
          }
          const span = document.createElement("span");
          span.style.color = "#f85149";
          span.textContent = t("chat.error_prefix", "Error: {message}").replace("{message}", ev.message);
          assistantBody.appendChild(span);
        } else if (ev.type === "done") {
          if (ev.prompt_eval_count != null && ev.eval_count != null) {
            tokenCount = {
              prompt_eval_count: ev.prompt_eval_count,
              eval_count: ev.eval_count,
              total_count: ev.prompt_eval_count + ev.eval_count,
            };
          }
          if (ev.num_ctx != null) {
            numCtx = ev.num_ctx;
          }
          break;
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      assistantBody.textContent = t("chat.stopped");
    } else {
      assistantBody.textContent = t("chat.error_prefix", "Error: {message}").replace("{message}", err.message);
    }
    assistantDiv.dataset.rawText = assistantBody.textContent;
  }

  // Flush any trailing SSE data that did not end with a double newline.
  let hadTrailingDelta = false;
  if (buffer.trim()) {
    for (const payload of parseSseEvent(buffer)) {
      if (!payload.trim()) continue;
      let ev;
      try {
        ev = JSON.parse(payload);
      } catch {
        continue;
      }
      if (ev.type === "delta") {
        assistantText += ev.content;
        hadTrailingDelta = true;
      } else if (ev.type === "done") {
        if (ev.prompt_eval_count != null && ev.eval_count != null) {
          tokenCount = {
            prompt_eval_count: ev.prompt_eval_count,
            eval_count: ev.eval_count,
            total_count: ev.prompt_eval_count + ev.eval_count,
          };
        }
        if (ev.num_ctx != null) {
          numCtx = ev.num_ctx;
        }
      }
    }
  }
  // Cancel any pending animation frame and render the trailing text immediately
  // so the user does not see raw text linger before renderSession() rebuilds
  // the pane.
  if (hadTrailingDelta && streamRafId !== null) {
    cancelAnimationFrame(streamRafId);
    streamRafId = null;
  }
  if (assistantText) {
    flushStreamFrame();
  }

  if (!assistantText && !pendingTools.length && !thinkingBody && !gotError) {
    assistantBody.textContent = t("chat.no_response");
  }

  assistantDiv.dataset.rawText = assistantText || assistantBody.textContent || "";
  if (assistantCopyBtn) {
    assistantCopyBtn.hidden = false;
  }
  if (userCopyBtn) {
    userCopyBtn.hidden = false;
  }

  abortController = null;
  cancelBtn.disabled = true;
  sendBtn.disabled = false;
  inputEl.focus();

  // Preserve any error message that was rendered during the stream so it is
  // not lost when renderSession() rebuilds the chat pane.
  const errorHtml = gotError ? assistantBody.innerHTML : null;

  await selectSession(currentSessionId);

  if (tokenCount) {
    updateTokenGauge(tokenCount.prompt_eval_count, numCtx);
  }

  if (errorHtml && chatEl.lastElementChild) {
    const lastBody = chatEl.lastElementChild.querySelector(".body");
    if (lastBody && lastBody.textContent.trim() === t("chat.no_response")) {
      lastBody.innerHTML = errorHtml;
    }
  }
}

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  send();
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

async function clearMessages() {
  if (!currentSessionId) {
    chatEl.innerHTML = "";
    pendingFiles = [];
    renderAttachments();
    updateTokenGauge(0, 0);
    return;
  }

  try {
    const res = await csrfFetch(`/api/sessions/${currentSessionId}/messages`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    if (!data.cleared) throw new Error(t("chat.clear_failed"));

    if (currentSession) {
      currentSession.messages = [];
      currentSession.files = [];
    }
    pendingFiles = [];
    chatEl.innerHTML = "";
    renderAttachments();
    updateTokenGauge(0, 0);
  } catch (err) {
    console.error("failed to clear messages", err);
    alert(t("chat.clear_messages_failed"));
  }
}

clearBtn.addEventListener("click", clearMessages);

cancelBtn.addEventListener("click", () => {
  if (abortController) {
    abortController.abort();
  }
});

function updateTokenGauge(promptEvalCount, numCtx) {
  const gaugeEl = document.getElementById("token-gauge");
  const textEl = gaugeEl ? gaugeEl.querySelector(".gauge-text") : null;
  const fgEl = gaugeEl ? gaugeEl.querySelector(".gauge-fg") : null;
  if (!gaugeEl || !textEl || !fgEl) return;

  if (!numCtx) {
    gaugeEl.style.display = "none";
    return;
  }
  gaugeEl.style.display = "";

  const ratio = Math.min(Math.max(promptEvalCount / numCtx, 0), 1);
  const dash = ratio * 100;
  const gap = 100 - dash;
  fgEl.style.strokeDasharray = `${dash} ${gap}`;
  fgEl.style.strokeDashoffset = "0";

  textEl.textContent = (promptEvalCount / 1000).toFixed(1) + "k";

  if (ratio >= 0.8) {
    fgEl.style.stroke = "var(--danger)";
  } else if (ratio >= 0.5) {
    fgEl.style.stroke = "#f0883e";
  } else {
    fgEl.style.stroke = "var(--accent)";
  }
}

function updateTokenGaugeFromSession() {
  if (!currentSession) {
    updateTokenGauge(0, 0);
    return;
  }
  const tc = currentSession.token_count;
  const messages = currentSession.messages || [];
  const assistantMessages = messages.filter((m) => m.role === "assistant");

  if (tc && tc.num_ctx) {
    updateTokenGauge(tc.prompt_eval_count || 0, tc.num_ctx || 0);
    return;
  }

  // Fall back to the most recent assistant message token_count when the
  // session aggregate is missing (e.g. older sessions).
  let fallback = null;
  for (let i = assistantMessages.length - 1; i >= 0; i--) {
    const mtc = assistantMessages[i].token_count;
    if (mtc && mtc.num_ctx) {
      fallback = mtc;
      break;
    }
  }
  if (fallback) {
    updateTokenGauge(fallback.prompt_eval_count || 0, fallback.num_ctx || 0);
    return;
  }

  // Hide gauge only when the session has no chat history at all.
  if (messages.length === 0) {
    updateTokenGauge(0, 0);
    return;
  }

  // Keep the current gauge value for sessions that have messages but no
  // token data yet, so the gauge does not flicker during session switching.
}

loadSessions();
loadModelCapabilities();