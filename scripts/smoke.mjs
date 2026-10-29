import assert from "node:assert/strict";

const origin = process.env.SMOKE_ORIGIN ?? "http://127.0.0.1:3000";

async function get(path) {
  return fetch(new URL(path, origin), { signal: AbortSignal.timeout(10_000) });
}

const page = await get("/");
assert.equal(page.status, 200, "Web shell must respond");
assert.match(await page.text(), /CGL.*Workspace/s, "Web shell must render");

for (const [path, status] of [
  ["/api/v1/health/live", "ok"],
  ["/api/v1/health/ready", "ready"],
]) {
  const response = await get(path);
  assert.equal(response.status, 200, `${path} must be reachable through the web origin`);
  const body = await response.json();
  assert.equal(body.service, "cgl-api");
  assert.equal(body.status, status);
  assert.equal(typeof body.version, "string");
  assert.equal(response.headers.get("cache-control"), "no-store");
}

console.log("Smoke check passed: web shell and same-origin API health/readiness.");
