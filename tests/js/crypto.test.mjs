// Run: node tests/js/crypto.test.mjs   (Node 20+: WebCrypto and atob are built in)
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
await import(join(here, "../../dashboard/crypto.js"));
const { decryptBlob, deriveKey, encryptWithKey, decryptWithKey } = globalThis.JRCrypto;
const vec = JSON.parse(readFileSync(join(here, "../vectors/crypto_vector.json"), "utf8"));

// 1. The Python-encrypted vector decrypts in WebCrypto to the same plaintext.
assert.deepEqual(await decryptBlob(vec.blob, vec.password), vec.plaintext);

// 2. A wrong passphrase fails (GCM authentication).
await assert.rejects(decryptBlob(vec.blob, "not the password"));

// 3. Tampered ciphertext fails.
const bad = { ...vec.blob, ct: vec.blob.ct.slice(0, -4) + (vec.blob.ct.endsWith("AAAA") ? "BBBB" : "AAAA") };
await assert.rejects(decryptBlob(bad, vec.password));

// 4. Local-storage box round trip with a derived key.
const key = await deriveKey("another passphrase", crypto.getRandomValues(new Uint8Array(16)), 1000);
const box = await encryptWithKey(key, { token: "x", overrides: { a: 1 } });
assert.deepEqual(await decryptWithKey(key, box), { token: "x", overrides: { a: 1 } });

console.log("js crypto tests: 4 passed");
