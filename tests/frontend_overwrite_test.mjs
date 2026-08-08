import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(
    new URL("../public/uplivion.js", import.meta.url),
    "utf8"
);
const storage = new Map();
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
    sessionStorage: {
        getItem(key) {
            return storage.get(key) ?? null;
        },
        setItem(key, value) {
            storage.set(key, String(value));
        },
        removeItem(key) {
            storage.delete(key);
        },
    },
    Headers,
    TextEncoder,
    DOMException,
    FormData,
    Blob,
    setTimeout,
    clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(
    `
    progressBar = {style: {}};
    progressBarText = {textContent: ""};
    quotaMessage = {textContent: ""};
    `,
    context
);

const completedID = "user-id_00000000-0000-4000-8000-000000000001";
const partialID = "user-id_00000000-0000-4000-8000-000000000002.part";
const requests = [];
context.overwriteOverlay = async () => true;
context.fetchProtectedResponse = async (url, options) => {
    assert.equal(url, "/upload");
    requests.push(options.body);
    if (requests.length === 1) {
        return {
            status: 409,
            ok: false,
            json: async () => ({
                filename: "same.bin",
                fileID: completedID,
                size: 3,
                uploaded: "now",
                hash: "old-hash",
            }),
        };
    }
    return {
        status: 200,
        ok: true,
        json: async () => ({
            fileID: partialID,
            url: "https://example.invalid/share/token",
        }),
    };
};

const file = new Blob(["new"]);
Object.defineProperties(file, {
    name: {value: "same.bin"},
    lastModified: {value: 1},
});

await context.upload(file, 3600, undefined);

assert.equal(requests.length, 2);
assert.equal(requests[0].get("overwrite"), "0");
assert.equal(requests[0].has("uploadID"), false);
assert.equal(requests[1].get("overwrite"), "1");
assert.equal(requests[1].has("uploadID"), false);

assert.equal(storage.size, 0);

const legacyFile = new Blob(["old-state"]);
Object.defineProperties(legacyFile, {
    name: {value: "legacy.bin"},
    lastModified: {value: 2},
});
const legacyResumeKey = "uplivion-upload:legacy.bin:9:2";
storage.set(legacyResumeKey, completedID);
let progressCalls = 0;
let recoveredRequest;
context.fetchProtected = async () => {
    progressCalls += 1;
    const error = new Error("Missing or invalid upload ID");
    error.status = 400;
    throw error;
};
context.fetchProtectedResponse = async (url, options) => {
    assert.equal(url, "/upload");
    recoveredRequest = options.body;
    return {
        status: 200,
        ok: true,
        json: async () => ({
            fileID: partialID,
            url: "https://example.invalid/share/recovered",
        }),
    };
};

await context.upload(legacyFile, 3600, undefined);

assert.equal(progressCalls, 1);
assert.equal(recoveredRequest.has("uploadID"), false);
assert.equal(storage.has(legacyResumeKey), false);

for (const [suffix, progressError] of [
    ["server", Object.assign(new Error("temporary failure"), {status: 500})],
    ["network", new Error("network unavailable")],
]) {
    const transientFile = new Blob(["retry"]);
    Object.defineProperties(transientFile, {
        name: {value: `${suffix}.bin`},
        lastModified: {value: suffix.length},
    });
    const resumeKey = `uplivion-upload:${suffix}.bin:5:${suffix.length}`;
    const validPartialID = (
        `user-id_00000000-0000-4000-8000-00000000000${suffix.length}.part`
    );
    storage.set(resumeKey, validPartialID);
    let transientRequest;
    context.fetchProtected = async () => {
        throw progressError;
    };
    context.fetchProtectedResponse = async (url, options) => {
        assert.equal(url, "/upload");
        transientRequest = options.body;
        return {
            status: 200,
            ok: true,
            json: async () => ({
                fileID: validPartialID,
                url: `https://example.invalid/share/${suffix}`,
            }),
        };
    };

    await context.upload(transientFile, 3600, undefined);

    assert.equal(transientRequest.get("uploadID"), validPartialID);
    assert.equal(storage.has(resumeKey), false);
}
