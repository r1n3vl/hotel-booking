const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const path = require("node:path");
const {test} = require("node:test");
const vm = require("node:vm");

const source = readFileSync(path.join(__dirname, "../../hotels/static/hotels/catalogue.js"), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function setup({available = true, supported = true} = {}) {
    const events = {};
    const calls = [];
    const pushes = [];
    const redirected = [];
    const attributes = {};
    const classes = new Set();
    let focused = false;
    let focusedElement;
    const focusAttributes = {};
    const focus = {tagName: "H1", setAttribute(name, value) {focusAttributes.heading = {[name]: value};}, focus() {focused = true; focusedElement = "H1";}};
    const invalidFocus = {tagName: "INPUT", setAttribute(name, value) {focusAttributes.input = {[name]: value};}, focus() {focused = true; focusedElement = "INPUT";}};
    const content = {innerHTML: "original results", querySelector(selector) {
        if (selector === "[aria-invalid='true']") return this.innerHTML === "invalid filters" ? invalidFocus : null;
        return focus;
    }};
    const feedback = {hidden: true, textContent: "", classList: {add: (name) => classes.add(name), remove: (name) => classes.delete(name)}};
    const retry = {hidden: true, addEventListener(name, fn) {events.retry = fn;}};
    const catalogue = {
        querySelector(selector) {return {"[data-catalogue-content]": content, "[data-catalogue-feedback]": feedback, "[data-catalogue-retry]": retry}[selector];},
        setAttribute(name, value) {attributes[name] = value;},
        addEventListener(name, fn) {events[name] = fn;},
    };
    const location = {href: "http://localhost/hotels/?page=3", assign: (url) => redirected.push(url)};
    const fetch = (url, options) => new Promise((resolve, reject) => calls.push({url, options, resolve, reject}));
    const window = {fetch: supported ? fetch : undefined, AbortController, location, addEventListener(name, fn) {events[name] = fn;}};
    const history = {pushState(state, title, url) {pushes.push(url); location.href = url;}};
    class FormData {
        constructor(form) {this.fields = form.fields;}
        [Symbol.iterator]() {return this.fields[Symbol.iterator]();}
    }
    vm.runInNewContext(source, {document: {querySelector: () => available ? catalogue : null}, window, history, fetch, AbortController, URL, URLSearchParams, FormData}, {filename: path.join(__dirname, "../../hotels/static/hotels/catalogue.js")});
    function submit(fields = [["city", "Warsaw"]]) {
        let prevented = false;
        events.submit({target: {matches: () => true, action: "http://localhost/hotels/", fields}, preventDefault() {prevented = true;}});
        return prevented;
    }
    function click(href, modifiers = {}) {
        let prevented = false;
        events.click({target: {closest: () => href ? {href} : null}, button: 0, ...modifiers, preventDefault() {prevented = true;}});
        return prevented;
    }
    async function respond(index = 0, data = {html: "updated results", count: 12, page: 2, valid: true}, extra = {}) {
        calls[index].resolve({ok: true, redirected: false, json: async () => data, ...extra});
        await flush();
    }
    return {events, calls, pushes, redirected, attributes, classes, content, feedback, retry, location, submit, click, respond, focused: () => focused, focusedElement: () => focusedElement, focusAttributes};
}

test("regular GET forms remain usable when fetch is unsupported or catalogue is absent", () => {
    assert.deepEqual(Object.keys(setup({supported: false}).events), []);
    assert.deepEqual(Object.keys(setup({available: false}).events), []);
});

test("filter submit uses authenticated GET AJAX, encoded fields and resets pagination", async () => {
    const app = setup();
    assert.equal(app.submit([["city", "Łódź & Warsaw"]]), true);
    const call = app.calls[0];
    assert.equal(call.url, "http://localhost/hotels/?city=%C5%81%C3%B3d%C5%BA+%26+Warsaw");
    assert.equal(call.options.credentials, "same-origin");
    assert.equal(call.options.headers["X-Requested-With"], "XMLHttpRequest");
    assert.equal(call.options.headers.Accept, "application/json");
    assert.equal(app.attributes["aria-busy"], "true");
    assert.equal(app.feedback.textContent, "Loading results…");
    await app.respond();
    assert.equal(app.content.innerHTML, "updated results");
    assert.equal(app.pushes.length, 1);
    assert.equal(app.attributes["aria-busy"], "false");
    assert.equal(app.focused(), true);
    assert.match(app.feedback.textContent, /12 results found/);
});

test("pagination and reset links load through AJAX", async () => {
    const app = setup();
    assert.equal(app.click("http://localhost/hotels/?city=Warsaw&page=2"), true);
    await app.respond();
    assert.equal(app.calls[0].url, "http://localhost/hotels/?city=Warsaw&page=2");
    app.click("http://localhost/hotels/");
    await app.respond(1);
    assert.equal(app.calls[1].url, "http://localhost/hotels/");
});

test("modified clicks and ordinary navigation are left to the browser", () => {
    const app = setup();
    for (const modifiers of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}]) {
        assert.equal(app.click("http://localhost/hotels/?page=2", modifiers), false);
    }
    assert.equal(app.click(null), false);
    assert.equal(app.calls.length, 0);
});

test("a later request aborts the earlier request and ignores its late response", async () => {
    const app = setup();
    app.submit([["city", "Warsaw"]]);
    app.submit([["city", "Krakow"]]);
    assert.equal(app.calls[0].options.signal.aborted, true);
    await app.respond(1, {html: "Krakow results", count: 1, page: 1, valid: true});
    await app.respond(0, {html: "Warsaw results", count: 99, page: 1, valid: true});
    assert.equal(app.content.innerHTML, "Krakow results");
    assert.equal(app.pushes.length, 1);
    assert.equal(app.location.href, "http://localhost/hotels/?city=Krakow");
});

test("network failure preserves old results and URL and offers a working retry", async () => {
    const app = setup();
    app.submit();
    app.calls[0].reject(new Error("Offline"));
    await flush();
    assert.equal(app.content.innerHTML, "original results");
    assert.equal(app.pushes.length, 0);
    assert.equal(app.retry.hidden, false);
    assert.match(app.feedback.textContent, /Previous results are still shown/);
    assert.equal(app.classes.has("request-error"), true);
    assert.equal(app.attributes["aria-busy"], "false");
    app.events.retry();
    assert.equal(app.calls[1].url, app.calls[0].url);
    await app.respond(1);
    assert.equal(app.retry.hidden, true);
    assert.equal(app.classes.has("request-error"), false);
    assert.equal(app.content.innerHTML, "updated results");
});

test("HTTP permission failure does not inject an HTML error page", async () => {
    const app = setup();
    app.submit();
    await app.respond(0, null, {ok: false, status: 403});
    assert.equal(app.content.innerHTML, "original results");
    assert.equal(app.retry.hidden, false);
});

test("session expiry navigates to the returned login URL", async () => {
    const app = setup();
    app.submit();
    await app.respond(0, null, {redirected: true, url: "http://localhost/accounts/login/?next=/hotels/"});
    assert.equal(app.redirected[0], "http://localhost/accounts/login/?next=/hotels/");
    assert.equal(app.content.innerHTML, "original results");
});

test("malformed JSON and wrong payloads fail without overwriting results", async () => {
    for (const response of [{html: null, count: 1}, {html: "bad", count: "1"}]) {
        const app = setup();
        app.submit();
        await app.respond(0, response);
        assert.equal(app.content.innerHTML, "original results");
        assert.equal(app.retry.hidden, false);
    }
    const app = setup();
    app.submit();
    await app.respond(0, null, {json: async () => {throw new SyntaxError("invalid JSON");}});
    assert.equal(app.retry.hidden, false);
});

test("server validation errors are rendered and announced", async () => {
    const app = setup();
    app.submit();
    await app.respond(0, {html: "invalid filters", count: 0, page: 1, valid: false});
    assert.equal(app.content.innerHTML, "invalid filters");
    assert.match(app.feedback.textContent, /Correct the filters/);
    assert.equal(app.focusedElement(), "INPUT");
    assert.equal(app.focusAttributes.input, undefined);
    assert.equal(app.retry.hidden, true);
});

test("back and forward restore filters without adding another history entry", async () => {
    const app = setup();
    app.location.href = "http://localhost/hotels/?city=Krakow";
    app.events.popstate();
    assert.equal(app.calls[0].url, app.location.href);
    await app.respond();
    assert.equal(app.pushes.length, 0);
    assert.equal(app.content.innerHTML, "updated results");
});

test("empty results are successful and reloading the same URL does not duplicate history", async () => {
    const app = setup();
    app.location.href = "http://localhost/hotels/?city=Warsaw";
    app.submit();
    await app.respond(0, {html: "No hotels match", count: 0, page: 1, valid: true});
    assert.equal(app.content.innerHTML, "No hotels match");
    assert.match(app.feedback.textContent, /0 results found/);
    assert.equal(app.pushes.length, 0);
});

test("aborted requests do not display a spurious failure", async () => {
    const app = setup();
    app.submit();
    const error = new Error("aborted");
    error.name = "AbortError";
    app.calls[0].reject(error);
    await flush();
    assert.equal(app.retry.hidden, true);
    assert.equal(app.classes.has("request-error"), false);
});
