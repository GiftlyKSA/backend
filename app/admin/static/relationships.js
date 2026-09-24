"use strict";

document.querySelectorAll(".relationship-field").forEach((field) => {
  const search = field.querySelector(".relationship-search");
  const select = field.querySelector("select");
  const more = field.querySelector(".relationship-more");
  const status = field.querySelector(".relationship-status");
  let cursor = null;
  let controller = null;
  let timer = null;

  async function load(append = false) {
    controller?.abort();
    const active = new AbortController();
    controller = active;
    more.disabled = true;
    status.textContent = "Loading records…";
    const url = new URL(field.dataset.url, window.location.origin);
    url.searchParams.set("search", search.value);
    if (field.dataset.recordId) url.searchParams.set("record_id", field.dataset.recordId);
    if (append && cursor) url.searchParams.set("after", cursor);
    try {
      const response = await fetch(url, { signal: active.signal, headers: { Accept: "application/json" } });
      if (!response.ok || response.redirected) throw new Error("Request failed");
      const result = await response.json();
      if (active.signal.aborted) return;
      if (!append) {
        Array.from(select.options).forEach((option) => {
          if (option.value && !option.selected) option.remove();
        });
      }
      result.items.forEach((item) => {
        if (!Array.from(select.options).some((option) => option.value === item.value)) {
          select.add(new Option(item.label, item.value));
        }
      });
      cursor = result.next_cursor;
      more.hidden = !cursor;
      status.textContent = result.items.length ? "Choose a record from the list." : "No matching available records.";
    } catch (error) {
      if (error.name !== "AbortError") status.textContent = "Could not load records. Search again to retry, or sign in again.";
    } finally {
      if (controller === active) more.disabled = false;
    }
  }

  search.addEventListener("input", () => {
    controller?.abort();
    clearTimeout(timer);
    timer = setTimeout(() => load(), 250);
  });
  more.addEventListener("click", () => load(true));
  load();
});
