"use strict";

const chatEl = document.getElementById("chat");
const formEl = document.getElementById("input-form");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const modelSelect = document.getElementById("model-select");
const thinkToggle = document.getElementById("think-toggle");
const clearBtn = document.getElementById("clear-btn");
const cancelBtn = document.getElementById("cancel-btn");

let messages = [];
let abortController = null;

function simpleMarkdownToHtml(text) {
  // Render plain text safely as HTML without external dependencies.
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
  const rawEvents = chunk.split("\n\n");
  for (const raw of rawEvents) {
    const lines = raw.split("\n");
    const dataLines = [];
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        dataLines.push(line.slice(6));
      }
    }
    if (dataLines.length) {
      events.push(dataLines.join(""));
    }
  }
  return events;
}

async function send() {
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  sendBtn.disabled = true;
  cancelBtn.disabled = false;
  abortController = new AbortController();

  messages.push({ role: "user", content: text });
  const userBody = addMessage("user").body;
  userBody.innerHTML = renderMarkdown(text);
  highlightIn(userBody);

  const assistantMsg = addMessage("assistant");
  const assistantBody = assistantMsg.body;
  const assistantExtras = assistantMsg.extras;
  assistantBody.textContent = "…";
  let assistantText = "";
  let thinkingBody = null;
  const pendingTools = [];

  const model = modelSelect.value;
  const think = thinkToggle.checked;

  const requestBody = JSON.stringify({ model, messages, think });
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

  // Remove the placeholder "…" as soon as real content arrives.
  let placeholderCleared = false;

  function appendDelta(content) {
    if (!placeholderCleared) {
      assistantBody.innerHTML = "";
      placeholderCleared = true;
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
      } else if (ev.type === "thinking") {
        if (!thinkingBody) thinkingBody = addThinkingBubble(assistantExtras);
        thinkingBody.textContent += ev.content;
        chatEl.scrollTop = chatEl.scrollHeight;
      } else if (ev.type === "delta") {
        appendDelta(ev.content);
      } else if (ev.type === "error") {
        assistantBody.innerHTML += `<span style="color:#f85149">エラー: ${ev.message}</span>`;
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

  if (!assistantText && !pendingTools.length && !thinkingBody) {
    assistantBody.textContent = "（応答なし）";
  }

  if (assistantText) {
    messages.push({ role: "assistant", content: assistantText });
  }
  abortController = null;
  cancelBtn.disabled = true;
  sendBtn.disabled = false;
  inputEl.focus();
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
  messages = [];
  chatEl.innerHTML = "";
});

cancelBtn.addEventListener("click", () => {
  if (abortController) {
    abortController.abort();
  }
});