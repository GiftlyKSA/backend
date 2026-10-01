const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");

const script = readFileSync("app/admin/static/datetime-fields.js", "utf8");

function picker(iso) {
  const stored = {};
  const input = { dataset: { iso }, value: "", parentElement: { querySelector: () => stored } };
  let submit;
  const form = {
    addEventListener: (name, handler) => { submit = handler; },
    querySelectorAll: () => [input],
  };
  runInNewContext(script, {
    Date,
    document: { querySelectorAll: (selector) => selector === "form" ? [form] : [input] },
  });
  return { input, stored, submit: () => submit() };
}

test("Riyadh picker shows UTC+3 and preserves the original instant if unchanged", () => {
  const current = picker("2026-10-01T23:30:00+00:00");
  assert.equal(current.input.value, "2026-10-02T02:30:00");
  current.submit();
  assert.equal(current.stored.value, "2026-10-01T23:30:00+00:00");
});

test("new Riyadh time converts back to UTC independently of browser timezone", () => {
  const current = picker("");
  current.input.value = "2026-10-02T02:30:00";
  current.submit();
  assert.equal(current.stored.value, "2026-10-01T23:30:00.000Z");
});
