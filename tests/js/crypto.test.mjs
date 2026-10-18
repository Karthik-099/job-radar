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

// 5. v2 (gzipped JSON) decrypts, as Python writes it.
{
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const k = await deriveKey("pass phrase v2", salt, 1000);
  const gz = new Uint8Array(await new Response(new Blob([JSON.stringify({ jobs: [1, 2, 3], s: "é" })]).stream()
    .pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, k, gz));
  const { b64e } = globalThis.JRCrypto;
  const blob = { v: 2, salt: b64e(salt), iv: b64e(iv), iters: 1000, ct: b64e(ct) };
  assert.deepEqual(await decryptBlob(blob, "pass phrase v2"), { jobs: [1, 2, 3], s: "é" });
}

console.log("js crypto tests: 5 passed");
