"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const modal = $("settings-modal");
  const screens = [...document.querySelectorAll("[data-settings-screen]")];
  const advanced = [...document.querySelectorAll("[data-option]")];
  const ctxValues = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
  const ctxLabels = ["4k", "8k", "16k", "32k", "64k", "128k", "256k"];
  let screen = "general";

  function cookie(name) {
    const found = document.cookie.split(";").map((v) => v.trim())
      .find((v) => v.startsWith(`${name}=`));
    return found ? decodeURIComponent(found.slice(name.length + 1)) : "";
  }
  async function request(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if ((options.method || "GET").toUpperCase() !== "GET") {
      headers["X-CSRF-Token"] = cookie("ollama_web_csrf");
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401) location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
    return response;
  }
  function status(message, error = false) {
    $("settings-status").textContent = message;
    $("settings-status").classList.toggle("error", error);
  }
  function show(name) {
    screen = name;
    screens.forEach((el) => { el.hidden = el.dataset.settingsScreen !== name; });
    $("settings-back-btn").hidden = name === "general";
    $("settings-modal-footer").hidden = name === "general";
    const key = name === "ui" ? "settings.ui.title"
      : name === "ollama" ? "settings.ollama.title" : "settings.general.title";
    $("settings-modal-title").textContent = t(key);
    status("");
  }
  function sliders() {
    $("temperature-value").textContent = Number($("settings-temperature").value).toFixed(1);
    $("num-ctx-value").textContent = ctxLabels[Number($("settings-num-ctx").value)];
  }
  function seedMode() {
    const fixed = $("settings-seed-mode").value === "fixed";
    $("settings-seed").hidden = !fixed;
    $("settings-seed").required = fixed;
    if (!fixed) $("settings-seed").value = "";
  }
  function populate(data) {
    const options = data.ollama.options;
    $("settings-language").value = data.ui.language;
    $("settings-system-prompt").value = data.ollama.system_prompt;
    $("settings-temperature").value = String(options.temperature);
    $("settings-num-ctx").value = String(Math.max(0, ctxValues.indexOf(options.num_ctx)));
    $("settings-seed-mode").value = options.seed === undefined ? "random" : "fixed";
    $("settings-seed").value = options.seed === undefined ? "" : String(options.seed);
    advanced.forEach((input) => {
      input.value = options[input.dataset.option] === undefined
        ? "" : String(options[input.dataset.option]);
    });
    $("settings-stop").value = Array.isArray(options.stop) ? options.stop.join("\n") : "";
    sliders();
    seedMode();
  }
  function number(input, integer) {
    const raw = input.value.trim();
    if (!raw) return null;
    const valid = integer ? /^-?\d+$/.test(raw) : Number.isFinite(Number(raw));
    input.setCustomValidity(valid ? "" : t(integer
      ? "settings.messages.invalid_integer" : "settings.messages.invalid_number"));
    if (!input.reportValidity()) throw new Error(input.validationMessage);
    return integer ? Number.parseInt(raw, 10) : Number(raw);
  }
  function collect() {
    const options = {
      temperature: Number($("settings-temperature").value),
      num_ctx: ctxValues[Number($("settings-num-ctx").value)],
    };
    if ($("settings-seed-mode").value === "fixed") {
      const value = number($("settings-seed"), true);
      if (value === null) {
        $("settings-seed").setCustomValidity(t("settings.messages.invalid_integer"));
        $("settings-seed").reportValidity();
        throw new Error("seed");
      }
      options.seed = value;
    }
    advanced.forEach((input) => {
      input.setCustomValidity("");
      const value = number(input, input.dataset.type === "int");
      if (value !== null) options[input.dataset.option] = value;
    });
    const stop = $("settings-stop").value.split(/\r?\n/).filter((line) => line.length);
    if (stop.length) options.stop = stop;
    return {
      ui: { language: $("settings-language").value },
      ollama: { system_prompt: $("settings-system-prompt").value, options },
    };
  }
  async function open() {
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    show("general");
    try {
      const response = await request("/api/settings");
      if (!response.ok) throw new Error(response.statusText);
      populate(await response.json());
    } catch (error) {
      console.error(error);
      status(t("settings.messages.load_failed"), true);
    }
  }
  function close() {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }
  async function save() {
    let payload;
    try { payload = collect(); } catch { return; }
    $("settings-save-btn").disabled = true;
    try {
      const response = await request("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || response.statusText);
      if (body.ui.language !== document.documentElement.lang) {
        location.reload();
        return;
      }
      status(t("settings.messages.saved"));
    } catch (error) {
      status(t("settings.messages.save_failed").replace("{error}", error.message), true);
    } finally { $("settings-save-btn").disabled = false; }
  }

  $("general-settings-btn").addEventListener("click", open);
  $("settings-modal-close").addEventListener("click", close);
  $("settings-back-btn").addEventListener("click", () => show("general"));
  $("open-ui-settings").addEventListener("click", () => show("ui"));
  $("open-ollama-settings").addEventListener("click", () => show("ollama"));
  $("settings-save-btn").addEventListener("click", save);
  $("settings-temperature").addEventListener("input", sliders);
  $("settings-num-ctx").addEventListener("input", sliders);
  $("settings-seed-mode").addEventListener("change", seedMode);
  modal.addEventListener("click", (event) => { if (event.target === modal) close(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && modal.classList.contains("open")) close();
  });
})();
