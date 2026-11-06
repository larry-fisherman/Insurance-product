import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "../../..");
const tokensCss = readFileSync(path.join(root, "packages/web-config/tokens.css"), "utf8");

const TEXT = 4.5;
const BOUNDARY = 3;

type Rgb = [number, number, number];

const tokens = new Map(
  [...tokensCss.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]),
);

function hex(token: string): string {
  const value = tokens.get(token);
  if (value === undefined) throw new Error("Unknown token " + token);
  const reference = /^var\((--[\w-]+)\)$/.exec(value);
  if (reference) return hex(reference[1]);
  if (!/^#[0-9a-f]{6}$/i.test(value))
    throw new Error(token + " is not a color: " + value);
  return value.toLowerCase();
}

function channels(value: string): Rgb {
  return [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16)) as Rgb;
}

function alphaColor(token: string): { color: Rgb; alpha: number } {
  const match = /^rgb\((\d+) (\d+) (\d+) \/ ([\d.]+)\)$/.exec(tokens.get(token) ?? "");
  if (!match) throw new Error(token + " is not a translucent color");
  return {
    color: [Number(match[1]), Number(match[2]), Number(match[3])],
    alpha: Number(match[4]),
  };
}

function over(top: Rgb, alpha: number, base: Rgb): Rgb {
  return top.map((channel, i) => channel * alpha + base[i] * (1 - alpha)) as Rgb;
}

function luminance(color: Rgb): number {
  const [r, g, b] = color.map((channel) => {
    const c = channel / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function tokenRatio(foreground: string, background: string): number {
  return ratio(channels(hex(foreground)), channels(hex(background)));
}

const WHITE: Rgb = [255, 255, 255];

describe("palette pairings meet WCAG AA", () => {
  const light = ["--color-bg", "--color-surface-alt"];
  it.each([
    "--color-ink",
    "--color-text",
    "--color-muted",
    "--color-link",
    "--color-action",
    "--color-dark",
    "--color-dark-raised",
    "--color-accent",
  ])("%s is readable text on both light surfaces", (foreground) => {
    for (const background of light) {
      expect(tokenRatio(foreground, background)).toBeGreaterThanOrEqual(TEXT);
    }
  });

  it.each([
    ["--color-surface-alt", "--color-dark"],
    ["--color-surface-alt", "--color-action"],
    ["--color-surface-alt", "--color-dark-raised"],
    ["--color-on-action", "--color-action"],
    ["--color-on-action", "--color-link"],
    ["--color-on-action", "--color-accent"],
    ["--color-ink", "--color-bg"],
    ["--color-on-dark-muted", "--color-dark"],
    ["--color-on-dark-muted", "--color-action"],
    ["--color-on-dark-muted", "--color-dark-raised"],
  ])("%s on %s", (foreground, background) => {
    expect(tokenRatio(foreground, background)).toBeGreaterThanOrEqual(TEXT);
  });

  it("keeps sidebar active-row text readable over the translucent highlight", () => {
    const active = alphaColor("--color-dark-active");
    const row = over(active.color, active.alpha, channels(hex("--color-dark")));
    expect(ratio(channels(hex("--color-on-action")), row)).toBeGreaterThanOrEqual(TEXT);
    expect(ratio(channels(hex("--color-on-dark-muted")), row)).toBeGreaterThanOrEqual(
      TEXT,
    );
  });
});

describe("pairings the palette must not use stay documented", () => {
  it.each([
    ["--color-on-dark-muted", "--color-bg", TEXT],
    ["--color-on-dark-muted", "--color-surface-alt", TEXT],
    ["--color-on-dark-muted", "--color-bg", BOUNDARY],
    ["--color-on-dark-muted", "--color-surface-alt", BOUNDARY],
    ["--color-on-dark-muted", "--color-link", BOUNDARY],
    ["--color-accent", "--color-dark", TEXT],
  ])("%s on %s stays below %d", (foreground, background, threshold) => {
    expect(tokenRatio(foreground, background)).toBeLessThan(threshold);
  });
});

describe("control boundaries and focus rings", () => {
  it.each([
    ["--color-muted", "--color-bg"],
    ["--color-muted", "--color-surface-alt"],
    ["--color-focus", "--color-bg"],
    ["--color-focus", "--color-surface-alt"],
    ["--color-focus-on-dark", "--color-dark"],
    ["--color-focus-on-dark", "--color-action"],
  ])("%s on %s", (foreground, background) => {
    expect(tokenRatio(foreground, background)).toBeGreaterThanOrEqual(BOUNDARY);
  });
});

describe("hero text stays legible over any image", () => {
  const offWhite = channels(hex("--color-surface-alt"));

  it("passes on a pure white pixel under the default scrim", () => {
    const scrim = alphaColor("--hero-scrim");
    const worstCase = over(scrim.color, scrim.alpha, WHITE);
    expect(scrim.alpha).toBeGreaterThanOrEqual(0.65);
    expect(ratio(offWhite, worstCase)).toBeGreaterThanOrEqual(TEXT);
  });

  it("passes on a pure white pixel under the nav surface alone", () => {
    const nav = alphaColor("--hero-nav-surface");
    const worstCase = over(nav.color, nav.alpha, WHITE);
    expect(nav.alpha).toBeGreaterThanOrEqual(0.65);
    expect(ratio(offWhite, worstCase)).toBeGreaterThanOrEqual(TEXT);
  });

  it("documents the 0.65 minimum: 0.60 fails and 0.65 passes", () => {
    const { color } = alphaColor("--hero-scrim");
    expect(ratio(offWhite, over(color, 0.6, WHITE))).toBeLessThan(TEXT);
    expect(ratio(offWhite, over(color, 0.65, WHITE))).toBeGreaterThanOrEqual(TEXT);
  });
});

describe("status tokens", () => {
  const statuses = [
    "approved",
    "approved-panel",
    "error",
    "blocker",
    "conflict",
    "warning",
    "missing",
    "done",
    "neutral",
  ];

  it.each(statuses)("%s text is readable on its own background", (status) => {
    expect(
      tokenRatio(`--status-${status}-text`, `--status-${status}-bg`),
    ).toBeGreaterThanOrEqual(TEXT);
  });

  it.each(["approved-panel", "error", "blocker", "conflict", "warning", "missing"])(
    "%s text is readable on white",
    (status) => {
      expect(tokenRatio(`--status-${status}-text`, "--color-bg")).toBeGreaterThanOrEqual(
        TEXT,
      );
    },
  );

  it("keeps approved panel body text readable", () => {
    for (const foreground of ["--color-ink", "--color-muted"]) {
      expect(tokenRatio(foreground, "--status-approved-panel-bg")).toBeGreaterThanOrEqual(
        TEXT,
      );
    }
  });
});

describe("hero pairings composited over a white image", () => {
  const scrim = alphaColor("--hero-scrim");
  const worstCase = over(scrim.color, scrim.alpha, WHITE);

  it.each(["--color-surface-alt", "--color-on-action"])(
    "%s text under the scrim",
    (foreground) => {
      expect(ratio(channels(hex(foreground)), worstCase)).toBeGreaterThanOrEqual(TEXT);
    },
  );

  it("keeps the on-dark focus ring visible under the scrim", () => {
    expect(
      ratio(channels(hex("--color-focus-on-dark")), worstCase),
    ).toBeGreaterThanOrEqual(BOUNDARY);
  });

  it.each([
    ["--color-ink", "--color-surface-alt"],
    ["--color-ink", "--color-bg"],
    ["--color-on-action", "--color-accent"],
  ])("opaque hero control %s on %s", (foreground, background) => {
    expect(tokenRatio(foreground, background)).toBeGreaterThanOrEqual(TEXT);
  });
});

describe("computed ratios are pinned to two decimals", () => {
  const pinned = (value: number) => value.toFixed(2);

  it.each([
    ["--color-ink", "--color-bg", "17.88"],
    ["--color-ink", "--color-surface-alt", "16.48"],
    ["--color-dark", "--color-bg", "16.37"],
    ["--color-dark", "--color-surface-alt", "15.08"],
    ["--color-action", "--color-bg", "12.99"],
    ["--color-action", "--color-surface-alt", "11.97"],
    ["--color-text", "--color-bg", "12.99"],
    ["--color-text", "--color-surface-alt", "11.96"],
    ["--color-dark-raised", "--color-bg", "12.72"],
    ["--color-dark-raised", "--color-surface-alt", "11.72"],
    ["--color-link", "--color-bg", "8.08"],
    ["--color-link", "--color-surface-alt", "7.44"],
    ["--color-muted", "--color-bg", "7.98"],
    ["--color-muted", "--color-surface-alt", "7.35"],
    ["--color-accent", "--color-bg", "5.21"],
    ["--color-accent", "--color-surface-alt", "4.80"],
    ["--color-on-dark-muted", "--color-bg", "2.81"],
    ["--color-on-dark-muted", "--color-surface-alt", "2.59"],
    ["--color-surface-alt", "--color-dark", "15.08"],
    ["--color-surface-alt", "--color-action", "11.97"],
    ["--color-surface-alt", "--color-dark-raised", "11.72"],
    ["--color-on-action", "--color-action", "12.99"],
    ["--color-on-action", "--color-link", "8.08"],
    ["--color-on-action", "--color-accent", "5.21"],
    ["--color-on-dark-muted", "--color-dark", "5.82"],
    ["--color-on-dark-muted", "--color-action", "4.62"],
    ["--color-on-dark-muted", "--color-dark-raised", "4.52"],
    ["--color-on-dark-muted", "--color-link", "2.87"],
    ["--color-accent", "--color-dark", "3.14"],
    ["--color-focus", "--color-bg", "17.88"],
    ["--color-focus", "--color-surface-alt", "16.48"],
    ["--color-focus-on-dark", "--color-dark", "15.08"],
    ["--color-focus-on-dark", "--color-action", "11.97"],
    ["--status-approved-text", "--status-approved-bg", "8.37"],
    ["--status-approved-panel-text", "--status-approved-panel-bg", "7.18"],
    ["--status-approved-panel-text", "--color-bg", "8.37"],
    ["--status-error-text", "--status-error-bg", "6.83"],
    ["--status-error-text", "--color-bg", "7.87"],
    ["--status-blocker-text", "--status-blocker-bg", "8.24"],
    ["--status-blocker-text", "--color-bg", "9.64"],
    ["--status-conflict-text", "--status-conflict-bg", "6.42"],
    ["--status-conflict-text", "--color-bg", "7.29"],
    ["--status-warning-text", "--status-warning-bg", "7.74"],
    ["--status-warning-text", "--color-bg", "9.30"],
    ["--status-missing-text", "--status-missing-bg", "7.34"],
    ["--status-missing-text", "--color-bg", "8.47"],
    ["--status-done-text", "--status-done-bg", "16.48"],
    ["--status-neutral-text", "--status-neutral-bg", "7.35"],
    ["--color-ink", "--status-approved-panel-bg", "15.35"],
    ["--color-muted", "--status-approved-panel-bg", "6.85"],
  ])("%s on %s is %s", (foreground, background, expected) => {
    expect(pinned(tokenRatio(foreground, background))).toBe(expected);
  });

  it.each([
    ["--color-surface-alt", "--hero-scrim", "5.11"],
    ["--color-on-action", "--hero-scrim", "5.55"],
    ["--color-surface-alt", "--hero-nav-surface", "6.10"],
  ])("%s over %s composited on white is %s", (foreground, surface, expected) => {
    const { color, alpha } = alphaColor(surface);
    expect(pinned(ratio(channels(hex(foreground)), over(color, alpha, WHITE)))).toBe(
      expected,
    );
  });

  it("pins the scrim limits on a white pixel", () => {
    const { color } = alphaColor("--hero-scrim");
    const offWhite = channels(hex("--color-surface-alt"));
    expect(pinned(ratio(offWhite, over(color, 0.6, WHITE)))).toBe("4.31");
    expect(pinned(ratio(offWhite, over(color, 0.65, WHITE)))).toBe("5.11");
  });

  it("pins the sidebar active row", () => {
    const active = alphaColor("--color-dark-active");
    const row = over(active.color, active.alpha, channels(hex("--color-dark")));
    expect(pinned(ratio(channels(hex("--color-on-action")), row))).toBe("12.96");
    expect(pinned(ratio(channels(hex("--color-on-dark-muted")), row))).toBe("4.60");
  });
});

describe("every color lives in tokens.css", () => {
  const stylesheets = [
    "apps/web/src/app/globals.css",
    "apps/marketing/src/app/globals.css",
    "apps/marketing/src/app/home.css",
  ];

  it.each(stylesheets)("%s has no stray colors or Georgia", (file) => {
    const css = readFileSync(path.join(root, file), "utf8").replace(
      /\/\*[\s\S]*?\*\//g,
      "",
    );
    expect(css.match(/#[0-9a-f]{3,8}\b/gi)).toBeNull();
    expect(css.match(/(?<![\w-])white(?![\w-])/gi)).toBeNull();
    expect(css.match(/\b(?:rgba?|hsla?)\(/gi)).toBeNull();
    expect(css.match(/georgia/gi)).toBeNull();
  });

  it("does not treat white-space as a color", () => {
    expect("white-space: nowrap".match(/(?<![\w-])white(?![\w-])/gi)).toBeNull();
  });
});
