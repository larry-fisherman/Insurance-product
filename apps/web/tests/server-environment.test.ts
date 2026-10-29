import { describe, expect, it } from "vitest";

import { readServerEnvironment } from "../src/lib/server-environment";

describe("server configuration", () => {
  it("fails clearly when the API origin is missing", () => {
    expect(() => readServerEnvironment({})).toThrow("API_INTERNAL_URL is required");
  });

  it.each([
    "not a URL",
    "file:///private/tmp/api",
    "http://user:password@localhost:8000",
    "http://localhost:8000/api",
    "http://localhost:8000?token=secret",
    "http://localhost:8000#fragment",
  ])("rejects invalid origins without echoing the value: %s", (origin) => {
    expect(() => readServerEnvironment({ API_INTERNAL_URL: origin })).toThrow(
      /HTTP\(S\)/,
    );
    try {
      readServerEnvironment({ API_INTERNAL_URL: origin });
    } catch (error) {
      expect(String(error)).not.toContain(origin);
    }
  });

  it("normalizes a valid origin", () => {
    expect(readServerEnvironment({ API_INTERNAL_URL: "http://api:8000/" })).toEqual({
      apiOrigin: "http://api:8000",
    });
  });
});
