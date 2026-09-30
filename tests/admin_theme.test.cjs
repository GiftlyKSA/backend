const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");

const script = readFileSync("app/admin/static/theme.js", "utf8");

function page(saved, blocked = false) {
  const events = {};
  const attributes = {};
  const button = {
    dataset: { darkLabel: "Dark", lightLabel: "Light" },
    setAttribute: (name, value) => { attributes[name] = value; },
    addEventListener: (name, handler) => { events[name] = handler; },
  };
  const document = {
    documentElement: { dataset: { theme: "light" } },
    querySelectorAll: () => [button],
    addEventListener: (name, handler) => { events[name] = handler; },
  };
  const storage = { value: saved };
  runInNewContext(script, {
    document,
    window: { addEventListener: (name, handler) => { events[name] = handler; } },
    localStorage: {
      getItem: () => { if (blocked) throw Error("Blocked"); return storage.value; },
      setItem: (key, value) => { if (blocked) throw Error("Blocked"); storage.value = value; },
    },
  });
  events.DOMContentLoaded();
  return { root: document.documentElement, events, attributes, storage, button };
}

test("restores saved theme, switches both directions, and updates accessible labels", () => {
  const current = page("dark");
  assert.equal(current.root.dataset.theme, "dark");
  assert.equal(current.attributes["aria-label"], "Light");
  current.events.click();
  assert.equal(current.root.dataset.theme, "light");
  assert.equal(current.storage.value, "light");
  assert.equal(current.attributes["aria-pressed"], "false");
  current.events.click();
  assert.equal(current.storage.value, "dark");
  assert.equal(page(current.storage.value).root.dataset.theme, "dark");
});

test("ignores invalid saved values and synchronizes valid changes from other tabs", () => {
  const current = page("invalid");
  assert.equal(current.root.dataset.theme, "light");
  current.events.storage({ key: "giftly_admin_theme", newValue: "dark" });
  assert.equal(current.root.dataset.theme, "dark");
  current.events.storage({ key: "giftly_admin_theme", newValue: "invalid" });
  assert.equal(current.root.dataset.theme, "dark");
});

test("still switches when storage is blocked", () => {
  const current = page(null, true);
  current.events.click();
  assert.equal(current.root.dataset.theme, "dark");
});
