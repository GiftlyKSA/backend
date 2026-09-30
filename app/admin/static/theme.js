"use strict";

(() => {
  const key = "giftly_admin_theme";
  const root = document.documentElement;
  const valid = (theme) => theme === "light" || theme === "dark";
  const apply = (theme) => {
    root.dataset.theme = theme;
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const label = theme === "dark" ? button.dataset.lightLabel : button.dataset.darkLabel;
      button.setAttribute("aria-label", label);
      button.title = label;
      button.setAttribute("aria-pressed", String(theme === "dark"));
    });
  };
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) apply(saved);
  } catch {
    // Theme switching still works when browser storage is unavailable.
  }
  document.addEventListener("DOMContentLoaded", () => {
    apply(root.dataset.theme);
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const theme = root.dataset.theme === "dark" ? "light" : "dark";
        apply(theme);
        try {
          localStorage.setItem(key, theme);
        } catch {
          // Keep the selected theme for this page when persistence is blocked.
        }
      });
    });
  });
  window.addEventListener("storage", (event) => {
    if (event.key === key && valid(event.newValue)) apply(event.newValue);
  });
})();
