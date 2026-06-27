"use strict";

const form = document.getElementById("login-form");
const pinInput = document.getElementById("pin-input");
const errorEl = document.getElementById("login-error");
const ALLOWED_NEXT_PATHS = new Set(["/", "/chat", "/settings"]);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.textContent = "";
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin: pinInput.value }),
  });
  if (!res.ok) {
    errorEl.textContent = t("login.pin_invalid", "Invalid PIN");
    return;
  }
  const requestedNext = new URLSearchParams(window.location.search).get("next") || "/";
  const safeNext = ALLOWED_NEXT_PATHS.has(requestedNext) ? requestedNext : "/";
  window.location.assign(safeNext);
});
