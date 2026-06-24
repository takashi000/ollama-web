"use strict";

const form = document.getElementById("login-form");
const pinInput = document.getElementById("pin-input");
const errorEl = document.getElementById("login-error");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.textContent = "";
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin: pinInput.value }),
  });
  if (!res.ok) {
    errorEl.textContent = "PINが違います";
    return;
  }
  const next = new URLSearchParams(window.location.search).get("next") || "/";
  window.location.assign(next.startsWith("/") ? next : "/");
});
