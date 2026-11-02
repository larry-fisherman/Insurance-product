import { describe, expect, it } from "vitest";
import { publicOrigins } from "../../../packages/web-config/public-origins";
import { readServerEnvironment } from "../src/lib/server-environment";

describe("marketing and application cookie boundaries", () => {
  it.each([
    ["http://localhost:3000", "http://localhost:3001"],
    ["https://APP.example.invalid", "http://app.example.invalid:8080"],
    ["https://app.example.invalid.", "https://app.example.invalid"],
    ["http://[::1]:3000", "http://[::1]:3001"],
  ])("rejects shared hostnames (%s, %s)", (application, marketing) => {
    const environment = { APPLICATION_ORIGIN: application, MARKETING_ORIGIN: marketing };
    expect(() => publicOrigins(environment)).toThrow("different cookie hostnames");
    expect(() =>
      readServerEnvironment({ ...environment, API_INTERNAL_URL: "http://api:8000" }),
    ).toThrow("different cookie hostnames");
  });

  it("accepts distinct local and HTTPS hosts", () => {
    expect(publicOrigins({})).toEqual({
      appOrigin: "http://localhost:3000",
      marketingOrigin: "http://127.0.0.1:3001",
    });
    expect(
      publicOrigins({
        APPLICATION_ORIGIN: "https://app.example.invalid/",
        MARKETING_ORIGIN: "https://www.example.invalid",
      }).appOrigin,
    ).toBe("https://app.example.invalid");
  });
});
