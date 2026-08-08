import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(
    new URL("../public/uplivion.js", import.meta.url),
    "utf8"
);
const context = {
    console: {error() {}, warn() {}, log() {}},
    document: {
        documentElement: {style: {setProperty() {}}},
        addEventListener() {},
    },
    window: {
        innerHeight: 800,
        addEventListener() {},
    },
    Headers,
    TextEncoder,
    DOMException,
    FormData,
    setTimeout,
    clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context);

let releaseRefresh;
let refreshCalls = 0;
context.fetch = async (url) => {
    assert.equal(url, "/session");
    refreshCalls += 1;
    return new Promise((resolve) => {
        releaseRefresh = () => resolve({
            ok: true,
            status: 200,
            json: async () => ({access_token: "first-token"}),
        });
    });
};

const first = context.getAccessToken();
const second = context.getAccessToken();
await Promise.resolve();
assert.equal(refreshCalls, 1, "concurrent callers must share one rotation");
releaseRefresh();
assert.deepEqual(await Promise.all([first, second]), [
    "first-token",
    "first-token",
]);

let protectedCalls = 0;
let secondAuthorization;
context.fetch = async (url, options) => {
    if (url === "/session") {
        refreshCalls += 1;
        return {
            ok: true,
            status: 200,
            json: async () => ({access_token: "rotated-token"}),
        };
    }
    assert.equal(url, "/upload");
    protectedCalls += 1;
    if (protectedCalls === 1) return {ok: false, status: 401};
    secondAuthorization = options.headers.get("Authorization");
    return {ok: true, status: 200};
};

const response = await context.fetchProtectedResponse("/upload", {
    method: "POST",
    body: new FormData(),
});
assert.equal(response.status, 200);
assert.equal(protectedCalls, 2);
assert.equal(refreshCalls, 2);
assert.equal(secondAuthorization, "Bearer rotated-token");

const backendError = await context.parseResponseError({
    status: 400,
    json: async () => ({error: "Specific backend error"}),
});
assert.equal(backendError.message, "Specific backend error");
assert.equal(backendError.status, 400);
