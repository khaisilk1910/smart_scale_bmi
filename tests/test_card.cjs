"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const path = require("node:path");

const components = new Map();
class FakeElement {
  constructor() { this.shadowRoot = null; this.events = []; }
  attachShadow() {
    this.shadowRoot = {
      innerHTML: "",
      querySelectorAll: () => [],
      querySelector: () => null,
    };
    return this.shadowRoot;
  }
  dispatchEvent(event) { this.events.push(event); }
}
const sandbox = {
  HTMLElement: FakeElement,
  customElements: {
    define: (name, classType) => components.set(name, classType),
    get: (name) => components.get(name),
  },
  document: { createElement: (name) => ({ tagName: name }) },
  window: { customCards: [], setTimeout, clearTimeout },
  CustomEvent: class CustomEvent { constructor(type, data) { this.type = type; this.detail = data.detail; } },
  console: { debug() {}, info() {}, error() {} },
  setTimeout, clearTimeout,
};
const source = fs.readFileSync(path.join(__dirname, "..", "custom_components", "smart_scale_bmi", "frontend", "smart-scale-bmi-card.js"), "utf8");
vm.runInNewContext(source, sandbox, { timeout: 3000 });
const Card = components.get("smart-scale-bmi-card");
const Editor = components.get("smart-scale-bmi-card-editor");
assert.ok(Card && Editor && sandbox.window.customCards.length === 1);
const card = new Card();
assert.ok(card.shadowRoot);
assert.equal(Card.getStubConfig().type, undefined, "Stub config must omit the type");
assert.equal(card.getGridOptions().columns, 12);
assert.ok(card.getCardSize() > 0);
assert.throws(() => card.setConfig(null), /invalid card/);
card.setConfig({ auto_entities: true });
const XSS = '<img src=x onerror="alert(1)">';
card.hass = { states: {
  'sensor.test_bmi': { state: '23.1', attributes: {
    integration: 'smart_scale_bmi', entry_id: 'abc', name: XSS,
    warning: 'bình thường', weight_kg: 62.3, bmi: 23.1,
    height_m: 1.63, recent_measurements: [{id: 1, entry_id: 'abc', measured_at: '2026-11-07T12:00:00+00:00', weight_kg: 62.3, bmi: 23.1, warning: 'bình thường'}],
  }},
} };
assert.match(card.shadowRoot.innerHTML, /ha-card/);
assert.match(card.shadowRoot.innerHTML, /&lt;img src=x/);
assert.ok(!card.shadowRoot.innerHTML.includes(XSS));
assert.match(card.shadowRoot.innerHTML, /shadow/); // CSS lives under the shadow root
const editor = new Editor();
editor.setConfig({auto_entities: false, entities: ['sensor.test_bmi']});
assert.match(editor.shadowRoot.innerHTML, /manual_entities/);
assert.match(editor.shadowRoot.innerHTML, /sensor.test_bmi/);
assert.equal(card._getEntities().length, 1);
// Frequent HA state changes should reuse a short-lived discovery cache.
const firstDiscovery = card._autoEntityCache;
card._getEntities();
assert.strictEqual(card._autoEntityCache, firstDiscovery);
card.setConfig({auto_entities: true});
card._getEntities();
assert.notStrictEqual(card._autoEntityCache, firstDiscovery);
console.log("Lovelace card API / editor / XSS smoke test: OK");
