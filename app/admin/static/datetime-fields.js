"use strict";

document.querySelectorAll('input[type="datetime-local"][data-iso]').forEach((input) => {
  if (input.dataset.iso) {
    const instant = new Date(input.dataset.iso);
    if (!Number.isNaN(instant.getTime())) {
      const local = new Date(instant.getTime() - instant.getTimezoneOffset() * 60000);
      input.value = local.toISOString().slice(0, 19);
      input.dataset.initialLocal = input.value;
    }
  }
});

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    form.querySelectorAll('input[type="datetime-local"][data-iso]').forEach((input) => {
      const stored = input.parentElement.querySelector("[data-datetime-value]");
      stored.value = !input.value ? "" : input.value === input.dataset.initialLocal
        ? input.dataset.iso : new Date(input.value).toISOString();
    });
  });
});
