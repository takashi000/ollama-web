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

let currentSessionId = null;
let currentSession = null;
let pendingFiles = [];
let abortController = null;

function simpleMarkdownToHtml(text) {
  const el = document.createElement("div");
  el.setAttribute("style", "white-space:pre-wrap");
  el.textContent = text;
  return el.outerHTML;
}

function renderMarkdown(text) {
  if (typeof marked !== "undefined" && marked.parse) {
    try {
      const html = marked.parse(text);
      if (typeof html === "string") return html;
    } catch {
      // fall through
    }
  }
  return simpleMarkdownToHtml(text);
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

function addMessage(role) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role === "user" ? "あなた" : "アシスタント";
  const body = document.createElement("div");
  body.className = "body";
  div.append(roleEl, body);
  let extras = null;
  if (role === "assistant") {
    extras = document.createElement("div");
    extras.className = "extras";
    div.appendChild(extras);
  }
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return { body, extras };
}

function addToolBubble(parentExtras, name, args) {
  const det = document.createElement("details");
  det.className = "tool";
  const label = (TOOLS.find((t) => t.name === name) || { label: name }).label;
  const sum = document.createElement("summary");
  sum.textContent = `${label} を実行中…`;
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
  chatEl.scrollTop = chatEl.scrollHeight;
  return { det, sum, result };
}

function addThinkingBubble(parentExtras) {
  const det = document.createElement("details");
  det.className = "thinking";
  const sum = document.createElement("summary");
  sum.textContent = "思考プロセス";
  const body = document.createElement("div");
  det.append(sum, body);
  parentExtras.appendChild(det);
  chatEl.scrollTop = chatEl.scrollHeight;
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
    li.textContent = "セッションがありません";
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
    del.textContent = "削除";
    del.title = "セッションを削除";
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
    const res = await fetch("/api/sessions", { method: "POST" });
    if (!res.ok) throw new Error(res.statusText);
    const session = await res.json();
    await loadSessions();
    await selectSession(session.id);
  } catch (err) {
    console.error("failed to create session", err);
    alert("セッションの作成に失敗しました");
  }
}

async function deleteSession(id) {
  if (!confirm("このセッションを削除しますか？")) return;
  try {
    const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(res.statusText);
    if (currentSessionId === id) {
      currentSessionId = null;
      currentSession = null;
      chatEl.innerHTML = "";
      pendingFiles = [];
      renderAttachments();
    }
    await loadSessions();
  } catch (err) {
    console.error("failed to delete session", err);
    alert("セッションの削除に失敗しました");
  }
}

async function selectSession(id) {
  try {
    const res = await fetch(`/api/sessions/${id}`);
    if (!res.ok) throw new Error(res.statusText);
    const session = await res.json();
    currentSessionId = session.id;
    currentSession = session;
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
  if (content && content.startsWith("[ERROR]")) {
    const lines = content.slice(7).split("\n").filter((l) => l.trim());
    body.innerHTML = "";
    for (const line of lines) {
      const span = document.createElement("span");
      span.style.color = "#f85149";
      span.textContent = line;
      body.appendChild(span);
      body.appendChild(document.createElement("br"));
    }
  } else {
    body.innerHTML = renderMarkdown(content);
    highlightIn(body);
  }
}

function renderSession() {
  chatEl.innerHTML = "";
  if (!currentSession) return;
  const msgs = currentSession.messages || [];
  for (const m of msgs) {
    if (m.role === "user") {
      const body = addMessage("user").body;
      const content = m.content || "";
      body.innerHTML = renderMarkdown(content);
      highlightIn(body);
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
        info.textContent = `添付: ${names}`;
        body.appendChild(info);
      }
    } else if (m.role === "assistant") {
      const body = addMessage("assistant").body;
      _renderAssistantContent(body, m.content);
    }
  }
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
    const res = await fetch(`/api/sessions/${currentSessionId}/files`, {
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
    alert("ファイルのアップロードに失敗しました");
  }
}

fileInput.addEventListener("change", () => {
  if (fileInput.files && fileInput.files.length) {
    uploadFiles(fileInput.files);
    fileInput.value = "";
  }
});

newSessionBtn.addEventListener("click", createSession);

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

  const fileIds = pendingFiles.map((f) => f.id);
  const userBody = addMessage("user").body;
  let displayContent = text;
  if (pendingFiles.length) {
    const names = pendingFiles.map((f) => f.name).join(", ");
    displayContent += (text ? "\n\n" : "") + `添付ファイル: ${names}`;
  }
  userBody.innerHTML = renderMarkdown(displayContent);
  highlightIn(userBody);

  pendingFiles = [];
  renderAttachments();

  const assistantMsg = addMessage("assistant");
  const assistantBody = assistantMsg.body;
  const assistantExtras = assistantMsg.extras;
  assistantBody.textContent = "…";
  let assistantText = "";
  let thinkingBody = null;
  const pendingTools = [];
  let gotError = false;

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
    res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: requestBody,
      signal: abortController.signal,
    });
  } catch (err) {
    assistantBody.textContent =
      err.name === "AbortError" ? "生成を停止しました" : `エラー: ${err.message}`;
    abortController = null;
    cancelBtn.disabled = true;
    sendBtn.disabled = false;
    return;
  }

  if (!res.ok || !res.body) {
    assistantBody.textContent = `エラー: ${res.status}`;
    abortController = null;
    cancelBtn.disabled = true;
    sendBtn.disabled = false;
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  let placeholderCleared = false;

  function appendDelta(content) {
    if (!placeholderCleared) {
      assistantBody.innerHTML = "";
      placeholderCleared = true;
    }
    // Remove the "waiting" placeholder once actual content starts arriving.
    const waiting = assistantBody.querySelector(".waiting-msg");
    if (waiting) {
      waiting.remove();
    }
    assistantText += content;
    assistantBody.innerHTML = renderMarkdown(assistantText);
    highlightIn(assistantBody);
    chatEl.scrollTop = chatEl.scrollHeight;
  }

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
          b.sum.textContent = b.sum.textContent.replace("実行中…", "完了");
          b.result.textContent = (ev.result || "").slice(0, 2000);
          chatEl.scrollTop = chatEl.scrollHeight;
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
          waiting.textContent = "ollama が回答を生成中…";
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
          waiting.textContent = ev.message || "処理中…";
          assistantBody.appendChild(waiting);
        }
      } else if (ev.type === "thinking") {
        if (!thinkingBody) thinkingBody = addThinkingBubble(assistantExtras);
        thinkingBody.textContent += ev.content;
        chatEl.scrollTop = chatEl.scrollHeight;
      } else if (ev.type === "delta") {
        appendDelta(ev.content);
      } else if (ev.type === "error") {
        gotError = true;
        if (!placeholderCleared) {
          assistantBody.innerHTML = "";
          placeholderCleared = true;
        }
        assistantBody.innerHTML += `<span style="color:#f85149">エラー: ${escapeHtml(ev.message)}</span>`;
      } else if (ev.type === "done") {
        break;
      }
    }
  }

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
      }
    }
    if (assistantText) {
      assistantBody.innerHTML = renderMarkdown(assistantText);
      highlightIn(assistantBody);
    }
  }

  if (!assistantText && !pendingTools.length && !thinkingBody && !gotError) {
    assistantBody.textContent = "（応答なし）";
  }

  abortController = null;
  cancelBtn.disabled = true;
  sendBtn.disabled = false;
  inputEl.focus();

  // Preserve any error message that was rendered during the stream so it is
  // not lost when renderSession() rebuilds the chat pane.
  const errorHtml = gotError ? assistantBody.innerHTML : null;

  await selectSession(currentSessionId);

  if (errorHtml && chatEl.lastElementChild) {
    const lastBody = chatEl.lastElementChild.querySelector(".body");
    if (lastBody && lastBody.textContent.trim() === "（応答なし）") {
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

clearBtn.addEventListener("click", () => {
  chatEl.innerHTML = "";
});

cancelBtn.addEventListener("click", () => {
  if (abortController) {
    abortController.abort();
  }
});

loadSessions();