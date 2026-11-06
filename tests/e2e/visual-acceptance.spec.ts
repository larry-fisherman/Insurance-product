import { readFileSync } from "node:fs";
import path from "node:path";
import {
  test,
  expect,
  type Browser,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

// Automated stand-ins for the redesign's manual checks. Screenshots are saved for
// a reader to judge; there are no pixel-diff baselines.
const appOrigin = process.env.CGL_WEB_ORIGIN ?? "http://localhost:3000";
const marketingOrigin = process.env.CGL_MARKETING_ORIGIN ?? "http://127.0.0.1:3001";
const reviewDirectory = ".artifacts/visual-review";
const axeSource = readFileSync(
  path.resolve(__dirname, "../../node_modules/axe-core/axe.min.js"),
  "utf8",
);
const widths = [1440, 390];
const FOCUS_RING_MIN_WIDTH = 2;
const FOCUS_RING_MIN_CONTRAST = 3;
const DIGIT_WIDTH_TOLERANCE = 0.01;
const LUMINANCE_MIN_DIFFERENCE = 0.2;
// Focus stops each page reached on November 4, so a walk that ends early fails.
const MIN_FOCUS_STOPS = {
  "marketing home at 1440px": 24,
  "marketing home at 390px": 18,
  login: 8,
  dashboard: 41,
};
// Long pages are saved at viewport height so a reader can see them at legible size.
const VIEWPORT_ONLY_SCREENSHOTS = new Set(["app-submission-data"]);

type Session = {
  storageState: Awaited<ReturnType<BrowserContext["storageState"]>>;
  submissionId: string;
};

// Kept in memory only, so the session cookie is never written to disk. One sign-in
// per worker: the backend allows five fictional workspaces a minute.
let session: Session | undefined;

async function signedIn(browser: Browser): Promise<Session> {
  if (session) return session;
  const context = await browser.newContext({ baseURL: appOrigin });
  const page = await context.newPage();
  await expect
    .poll(
      async () => {
        try {
          return (await page.request.get("/api/v1/health/ready", { timeout: 3000 })).ok();
        } catch {
          return false;
        }
      },
      { timeout: 90_000 },
    )
    .toBe(true);
  await page.goto("/login");
  await page.getByRole("button", { name: "Open a fictional example" }).click();
  await expect(page).toHaveURL(/submissions\/[0-9a-f-]+/, { timeout: 90_000 });
  const submissionId = page.url().split("/").at(-1)!;
  const me = await (await page.request.get("/api/v1/me")).json();
  await expect
    .poll(
      async () => {
        const response = await page.request.get("/api/v1/submissions/" + submissionId, {
          headers: { "X-Agency-Context": me.agency_context },
        });
        if (!response.ok()) return false;
        const data = await response.json();
        return (
          data.jobs.length === 4 &&
          data.jobs.every((job: { status: string }) => job.status === "completed")
        );
      },
      { timeout: 90_000 },
    )
    .toBe(true);
  session = { storageState: await context.storageState(), submissionId };
  await context.close();
  return session;
}

async function newPage(
  browser: Browser,
  origin: string,
  width: number,
  storageState?: Session["storageState"],
): Promise<Page> {
  const context = await browser.newContext({
    baseURL: origin,
    storageState,
    viewport: { width, height: width === 390 ? 844 : 1000 },
  });
  return context.newPage();
}

function watch(page: Page) {
  const issues = { errors: [] as string[], fontRequests: [] as string[] };
  page.on("console", (message) => {
    if (message.type() === "error") issues.errors.push(message.text());
  });
  page.on("pageerror", (error) => issues.errors.push(error.message));
  page.on("request", (request) => {
    const host = new URL(request.url()).hostname;
    if (host === "fonts.googleapis.com" || host === "fonts.gstatic.com") {
      issues.fontRequests.push(request.url());
    }
  });
  return issues;
}

async function settle(page: Page) {
  // A workspace may keep polling, so an idle network is waited for but not required.
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => undefined);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

// Moves the pointer to a viewport point with no control under it, so no hover
// style shows in a screenshot.
async function parkPointer(page: Page) {
  const point = await page.evaluate(() => {
    const controls = "a, button, input, select, textarea, summary, label, [role=button]";
    for (let y = 4; y < window.innerHeight; y += 24) {
      for (let x = window.innerWidth - 4; x > 0; x -= 24) {
        const element = document.elementFromPoint(x, y);
        if (element && !element.closest(controls)) return { x, y };
      }
    }
    return { x: 0, y: 0 };
  });
  await page.mouse.move(point.x, point.y);
}

async function runContrast(page: Page) {
  await page.evaluate(axeSource);
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: any }).axe;
    const results = await axe.run(document, {
      runOnly: { type: "rule", values: ["color-contrast"] },
    });
    return {
      passes: results.passes.flatMap((rule: any) => rule.nodes).length,
      incomplete: results.incomplete.flatMap((rule: any) => rule.nodes).length,
      violations: results.violations.flatMap((rule: any) =>
        rule.nodes.map((node: any) => ({
          target: node.target.join(" "),
          summary: node.failureSummary,
        })),
      ),
    };
  });
}

async function loadedFontFamilies(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    await document.fonts.ready;
    // next/font may also declare a "<family> Fallback" face over a local Arial; it
    // must not count as the real font having loaded.
    return [...document.fonts]
      .filter((face) => face.status === "loaded" && !/fallback/i.test(face.family))
      .map((face) => face.family);
  });
}

async function checkPage(
  page: Page,
  issues: ReturnType<typeof watch>,
  name: string,
  url: string,
  width: number,
) {
  await test.step(`${name} at ${width}px`, async () => {
    issues.errors.length = 0;
    issues.fontRequests.length = 0;
    const response = await page.goto(url);
    expect.soft(response?.ok(), `${name} loads`).toBe(true);
    await expect
      .soft(page.getByRole("heading", { level: 1 }).first(), `${name} shows its heading`)
      .toBeVisible();
    await settle(page);
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect
      .soft(overflow.scrollWidth, `${name} at ${width}px has no horizontal overflow`)
      .toBeLessThanOrEqual(overflow.innerWidth);
    await page.screenshot({
      path: `${reviewDirectory}/${name}-${width}.png`,
      fullPage: !VIEWPORT_ONLY_SCREENSHOTS.has(name),
    });
    if (name === "app-submission-data") {
      const conflictCard = page
        .locator(".review-card")
        .filter({ has: page.locator(`.badge:text-is("conflict")`) })
        .first();
      await expect.soft(conflictCard, `${name} shows a conflict card`).toBeVisible();
      // The card is taller than the viewport, so its top, with the heading and
      // conflict badge, is aligned to the top of the screen.
      await conflictCard.evaluate((card) => {
        card.scrollIntoView({ block: "start" });
        window.scrollBy(0, -16);
      });
      await parkPointer(page);
      await page.screenshot({
        path: `${reviewDirectory}/${name}-conflict-card-${width}.png`,
      });
      await page.evaluate(() => window.scrollTo(0, 0));
    }
    const contrast = await runContrast(page);
    console.log(
      `[contrast] ${name} at ${width}px: ${contrast.violations.length} violations, ` +
        `${contrast.passes} passed, ${contrast.incomplete} not judged by axe`,
    );
    expect.soft(contrast.violations, `${name} at ${width}px contrast`).toEqual([]);
    expect
      .soft(contrast.passes, `${name} at ${width}px has text axe could judge`)
      .toBeGreaterThan(0);
    const families = await loadedFontFamilies(page);
    expect.soft(families.join(), `${name} loaded Nunito`).toMatch(/nunito/i);
    expect.soft(families.join(), `${name} loaded DM Sans`).toMatch(/dm[ _]?sans/i);
    expect.soft(issues.fontRequests, `${name} requests no Google font`).toEqual([]);
    expect.soft(issues.errors, `${name} logs no console error`).toEqual([]);
  });
}

for (const width of widths) {
  test(`every page renders cleanly at ${width}px`, async ({ browser }) => {
    test.setTimeout(420_000);
    const marketing = await newPage(browser, marketingOrigin, width);
    const marketingIssues = watch(marketing);
    for (const [name, url] of [
      ["marketing-home", "/"],
      ["marketing-product", "/product"],
      ["marketing-how-it-works", "/how-it-works"],
      ["marketing-pricing", "/pricing"],
      ["marketing-request-demo", "/request-demo"],
      ["marketing-security", "/security"],
    ]) {
      await checkPage(marketing, marketingIssues, name, url, width);
      await expect
        .soft(marketing.locator(".environment"), `${name} keeps the preview banner`)
        .toBeVisible();
    }
    await marketing.context().close();

    const signedOut = await newPage(browser, appOrigin, width);
    await checkPage(signedOut, watch(signedOut), "app-login", "/login", width);
    await signedOut.context().close();

    const { storageState, submissionId } = await signedIn(browser);
    const app = await newPage(browser, appOrigin, width, storageState);
    const appIssues = watch(app);
    for (const [name, url] of [
      ["app-dashboard", "/dashboard"],
      ["app-insureds", "/insureds"],
      ["app-submissions", "/submissions"],
      ["app-submission", `/submissions/${submissionId}`],
      ["app-submission-data", `/submissions/${submissionId}/review`],
      ["app-tasks", "/tasks"],
      ["app-settings", "/settings/team"],
    ]) {
      await checkPage(app, appIssues, name, url, width);
    }
    await app.context().close();
  });
}

test("the hero scrim stays on top of a white image", async ({ browser }) => {
  const page = await newPage(browser, marketingOrigin, 1440);
  await page.goto("/");
  const whiteImage =
    'url("data:image/svg+xml,' +
    encodeURIComponent(
      "<svg xmlns='http://www.w3.org/2000/svg' width='1' height='1'>" +
        "<rect width='1' height='1' fill='white'/></svg>",
    ) +
    '")';
  const layers = await page.evaluate((image) => {
    document.documentElement.style.setProperty("--hero-media-image", image);
    const probe = document.createElement("div");
    probe.style.backgroundColor = "var(--hero-scrim)";
    document.body.append(probe);
    const scrim = getComputedStyle(probe).backgroundColor;
    probe.remove();
    const media = document.querySelector(".hero-media")!;
    const title = document.querySelector(".home .hero h1")!.getBoundingClientRect();
    const onTop = document.elementFromPoint(
      title.left + title.width / 2,
      title.top + title.height / 2,
    );
    return {
      scrim,
      background: getComputedStyle(media).backgroundImage,
      textIsAboveMedia: Boolean(onTop?.closest(".hero-copy")),
    };
  }, whiteImage);
  expect(layers.scrim).toBe("rgba(19, 25, 16, 0.65)");
  expect(
    layers.background.startsWith(
      `linear-gradient(${layers.scrim}, ${layers.scrim}), url(`,
    ),
  ).toBe(true);
  expect(layers.textIsAboveMedia).toBe(true);
  await page.screenshot({
    path: `${reviewDirectory}/marketing-home-white-hero-1440.png`,
  });
  await page.context().close();
});

async function measureDigits(target: Locator) {
  return target.evaluate(async (element) => {
    const probe = element.cloneNode(false) as HTMLElement;
    element.parentElement!.append(probe);
    const style = getComputedStyle(probe);
    await document.fonts.load(
      `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,
      "0123456789",
    );
    const digits = [...Array(10).keys()].map((digit) => {
      const span = document.createElement("span");
      span.textContent = String(digit);
      probe.append(span);
      return span.getBoundingClientRect().width;
    });
    // The first family in the stack is the one next/font loaded. Text set in it
    // with two different generic fallbacks measures the same only if that face
    // really renders; a face that failed to load or decode falls through.
    const primary = style.fontFamily
      .split(",")[0]
      .trim()
      .replace(/^["']|["']$/g, "");
    const sample = "0123456789 Submission";
    const widthWith = (generic: string) => {
      const span = document.createElement("span");
      span.textContent = sample;
      span.style.fontFamily = `"${primary}", ${generic}`;
      // Kept on one line and measured alone: in a narrow cell, wrapping would drop
      // the space at a line start and split the box.
      span.style.whiteSpace = "nowrap";
      span.style.display = "inline-block";
      probe.append(span);
      const width = span.getBoundingClientRect().width;
      span.remove();
      return width;
    };
    const monospace = widthWith("monospace");
    const serif = widthWith("serif");
    const loadedFaces = [...document.fonts].filter(
      (face) =>
        face.status === "loaded" &&
        face.family.replace(/^["']|["']$/g, "") === primary &&
        !/fallback/i.test(face.family),
    ).length;
    const result = {
      family: style.fontFamily,
      primary,
      loadedFaces,
      fallbackGap: Math.abs(monospace - serif),
      numeric: style.fontVariantNumeric,
      spread: Math.max(...digits) - Math.min(...digits),
    };
    probe.remove();
    return result;
  });
}

test("digits have one width in numeric values", async ({ browser }) => {
  test.setTimeout(240_000);
  const { storageState, submissionId } = await signedIn(browser);
  const page = await newPage(browser, appOrigin, 1440, storageState);
  await page.goto("/dashboard");
  const metric = page.locator(".metric strong").first();
  await expect(metric).toBeVisible();
  await page.goto(`/submissions/${submissionId}/review`);
  const factValue = page.locator(".fact-value").first();
  await expect(factValue).toBeVisible();
  await page.getByText("View cited spreadsheet cells", { exact: true }).first().click();
  const rawValue = page.locator("td.raw-value mark").first();
  await expect(rawValue).toBeVisible();
  const targets: [string, Locator][] = [
    [".fact-value", factValue],
    ["raw-value column", rawValue],
  ];
  const results = [];
  for (const [name, target] of targets) {
    results.push({ name, ...(await measureDigits(target)) });
  }
  await page.goto("/dashboard");
  await expect(metric).toBeVisible();
  results.push({ name: ".metric strong", ...(await measureDigits(metric)) });
  for (const result of results) {
    expect.soft(result.family, `${result.name} font`).toMatch(/nunito/i);
    expect.soft(result.primary, `${result.name} primary face`).toMatch(/nunito/i);
    expect.soft(result.primary, `${result.name} primary face`).not.toMatch(/fallback/i);
    expect
      .soft(result.loadedFaces, `${result.name} has a loaded ${result.primary} face`)
      .toBeGreaterThan(0);
    expect
      .soft(result.fallbackGap, `${result.name} renders in ${result.primary}`)
      .toBeLessThanOrEqual(DIGIT_WIDTH_TOLERANCE);
    expect.soft(result.numeric, `${result.name} numerals`).toContain("tabular-nums");
    expect
      .soft(result.spread, `${result.name} digit width spread`)
      .toBeLessThanOrEqual(DIGIT_WIDTH_TOLERANCE);
  }
  await page.context().close();
});

type StatusStyle = {
  text: string;
  glyph: string;
  background: string;
  color: string;
  border: string;
};

async function readStatus(target: Locator): Promise<StatusStyle> {
  await expect(target).toBeVisible();
  return target.evaluate((element) => {
    const style = getComputedStyle(element);
    const quoted = /"([^"]*)"/.exec(getComputedStyle(element, "::before").content);
    return {
      text: element.textContent!.trim(),
      glyph: quoted ? quoted[1] : "",
      background: style.backgroundColor,
      color: style.color,
      border: style.borderTopColor,
    };
  });
}

function relativeLuminance(color: string): number {
  const [r, g, b] = color
    .match(/[\d.]+/g)!
    .slice(0, 3)
    .map((part) => {
      const c = Number(part) / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// A close-up of the badge rows holding the conflict, failed, rejected, warning and
// approved tones, for a reader to judge at legible size.
async function saveBadgeRows(page: Page, width: number) {
  const rows = page
    .locator(".document-card .actions")
    .filter({ has: page.locator(".badge") });
  await rows.first().scrollIntoViewIfNeeded();
  await parkPointer(page);
  const boxes = await rows.evaluateAll((elements) =>
    elements
      .filter((element) =>
        [...element.querySelectorAll(".badge")].some((badge) =>
          ["failed", "rejected", "conflict", "quarantined", "approved"].includes(
            badge.textContent!.trim(),
          ),
        ),
      )
      .map((element) => {
        const box = element.getBoundingClientRect();
        return {
          left: box.left + window.scrollX,
          top: box.top + window.scrollY,
          right: box.right + window.scrollX,
          bottom: box.bottom + window.scrollY,
        };
      }),
  );
  expect(boxes.length, "badge rows to photograph").toBeGreaterThanOrEqual(3);
  const padding = 12;
  const x = Math.max(0, Math.min(...boxes.map((box) => box.left)) - padding);
  const y = Math.max(0, Math.min(...boxes.map((box) => box.top)) - padding);
  await page.screenshot({
    path: `${reviewDirectory}/app-status-badge-rows-${width}.png`,
    fullPage: true,
    clip: {
      x,
      y,
      width: Math.min(
        width - x,
        Math.max(...boxes.map((box) => box.right)) + padding - x,
      ),
      height: Math.max(...boxes.map((box) => box.bottom)) + padding - y,
    },
  });
}

test("statuses stay distinguishable without color", async ({ browser }) => {
  test.setTimeout(240_000);
  const { storageState, submissionId } = await signedIn(browser);
  const page = await newPage(browser, appOrigin, 1440, storageState);
  // The fictional example has no failed job, rejected scan or approved document, so
  // this tab's copy of the response carries those values through the real Status
  // component. Nothing here is sent to the backend.
  await page.route(
    new RegExp(`/api/v1/submissions/${submissionId}(\\?|$)`),
    async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const response = await route.fetch();
      const data = await response.json();
      [
        ["failed", "rejected"],
        ["conflict", "quarantined"],
        ["approved", "exported"],
      ].forEach(([processing, scan], index) => {
        data.documents[index].processing_status = processing;
        data.documents[index].scan_status = scan;
      });
      data.readiness.blockers = [
        { key: "fictional", message: "Fictional blocker", waivable: false },
      ];
      await route.fulfill({ response, json: data });
    },
  );
  const badge = (text: string) => page.locator(`.badge:text-is("${text}")`).first();
  await page.goto(`/submissions/${submissionId}/documents`);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  const failed = await readStatus(badge("failed"));
  const rejected = await readStatus(badge("rejected"));
  const conflict = await readStatus(badge("conflict"));
  const warning = await readStatus(badge("quarantined"));
  const approved = await readStatus(badge("approved"));
  const exported = await readStatus(badge("exported"));
  const primary = await page.evaluate(() => {
    const probe = document.createElement("button");
    probe.className = "primary";
    document.body.append(probe);
    const background = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return background;
  });
  await saveBadgeRows(page, 1440);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(badge("failed")).toBeVisible();
  await saveBadgeRows(page, 390);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/submissions/${submissionId}/packages`);
  const blocker = await readStatus(page.locator(".blocker-icon").first());

  expect.soft(failed.glyph).toBe("✕");
  expect.soft(conflict.glyph).toBe("⇄");
  expect.soft(warning.glyph).toBe("▲");
  expect.soft(approved.glyph).toBe("✔");
  expect.soft(blocker.text).toBe("!");
  expect.soft({ ...rejected, text: "" }).toEqual({ ...failed, text: "" });
  expect.soft({ ...exported, text: "" }).toEqual({ ...approved, text: "" });
  expect.soft(approved.background).not.toBe(primary);

  const named: [string, StatusStyle][] = [
    ["conflict", conflict],
    ["blocker", blocker],
    ["failed", failed],
    ["warning", warning],
    ["approved", approved],
  ];
  for (const [i, [firstName, first]] of named.entries()) {
    for (const [secondName, second] of named.slice(i + 1)) {
      // The label always differs, so the shape carries this check. A status whose
      // shape is page text (blocker) uses that text.
      const differsByShape =
        (first.glyph || first.text) !== (second.glyph || second.text);
      const luminanceGap = Math.abs(
        relativeLuminance(first.background) - relativeLuminance(second.background),
      );
      expect
        .soft(
          differsByShape || luminanceGap >= LUMINANCE_MIN_DIFFERENCE,
          `${firstName} and ${secondName} differ without color`,
        )
        .toBe(true);
    }
  }
  await page.context().close();
});

type FocusStop = {
  key: string;
  outlineStyle: string;
  outlineWidth: number;
  outlineAlpha: number;
  ringContrast: number;
  clippedBy: string[];
  inViewport: boolean;
};

function readFocus(): FocusStop | null {
  const element = document.activeElement as HTMLElement | null;
  if (!element || element === document.body || element === document.documentElement) {
    return null;
  }
  const parse = (color: string) => {
    const parts = color.match(/[\d.]+/g)!.map(Number);
    return { rgb: parts.slice(0, 3), alpha: parts[3] ?? 1 };
  };
  const luminance = (rgb: number[]) => {
    const [r, g, b] = rgb.map((channel) => {
      const c = channel / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const style = getComputedStyle(element);
  const width = parseFloat(style.outlineWidth);
  const offset = parseFloat(style.outlineOffset);
  const reach = width + offset;
  const box = element.getBoundingClientRect();
  const ring = {
    left: box.left - reach,
    right: box.right + reach,
    top: box.top - reach,
    bottom: box.bottom + reach,
  };
  const clippedBy: string[] = [];
  let ancestor = element.parentElement;
  while (
    ancestor &&
    ancestor !== document.body &&
    ancestor !== document.documentElement
  ) {
    const ancestorStyle = getComputedStyle(ancestor);
    const outer = ancestor.getBoundingClientRect();
    const left = outer.left + ancestor.clientLeft;
    const top = outer.top + ancestor.clientTop;
    const sides: string[] = [];
    if (ancestorStyle.overflowX !== "visible") {
      if (ring.left < left - 0.5) sides.push("left");
      if (ring.right > left + ancestor.clientWidth + 0.5) sides.push("right");
    }
    if (ancestorStyle.overflowY !== "visible") {
      if (ring.top < top - 0.5) sides.push("top");
      if (ring.bottom > top + ancestor.clientHeight + 0.5) sides.push("bottom");
    }
    if (sides.length) {
      clippedBy.push(`${ancestor.tagName}.${ancestor.className}: ${sides.join("+")}`);
    }
    ancestor = ancestor.parentElement;
  }
  // The surface the ring is drawn on: the control's own fill for an inset ring,
  // otherwise the nearest mostly opaque surface behind it.
  let surface: Element | null = offset < 0 ? element : element.parentElement;
  let background = { rgb: [255, 255, 255], alpha: 1 };
  while (surface) {
    const candidate = parse(getComputedStyle(surface).backgroundColor);
    if (candidate.alpha >= 0.5) {
      background = candidate;
      break;
    }
    surface = surface.parentElement;
  }
  // A translucent ring is blended over its surface before contrast is measured,
  // so a transparent outline scores 1:1 rather than as solid black.
  const outline = parse(style.outlineColor);
  const drawn = outline.rgb.map(
    (channel, i) => channel * outline.alpha + background.rgb[i] * (1 - outline.alpha),
  );
  const [hi, lo] = [luminance(drawn), luminance(background.rgb)].sort((a, b) => b - a);
  return {
    key:
      `${element.tagName} "${(element.innerText ?? "").trim().slice(0, 40)}" ` +
      `${element.getAttribute("href") ?? element.getAttribute("name") ?? ""}`,
    outlineStyle: style.outlineStyle,
    outlineWidth: width,
    outlineAlpha: outline.alpha,
    ringContrast: (hi + 0.05) / (lo + 0.05),
    clippedBy,
    inViewport:
      box.width > 0 &&
      box.height > 0 &&
      box.left >= -0.5 &&
      box.top >= -0.5 &&
      box.right <= window.innerWidth + 0.5 &&
      box.bottom <= window.innerHeight + 0.5,
  };
}

async function tabThrough(page: Page, label: string): Promise<FocusStop[]> {
  const stops: FocusStop[] = [];
  for (let press = 0; press < 200; press++) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(readFocus);
    if (!stop || stop.key === stops[0]?.key) break;
    stops.push(stop);
    expect
      .soft(stop.outlineStyle, `${label}: ${stop.key} has an outline`)
      .not.toBe("none");
    expect
      .soft(stop.outlineWidth, `${label}: ${stop.key} outline width`)
      .toBeGreaterThanOrEqual(FOCUS_RING_MIN_WIDTH);
    expect
      .soft(stop.outlineAlpha, `${label}: ${stop.key} outline is not transparent`)
      .toBeGreaterThan(0);
    expect
      .soft(stop.ringContrast, `${label}: ${stop.key} ring contrast`)
      .toBeGreaterThanOrEqual(FOCUS_RING_MIN_CONTRAST);
    expect.soft(stop.clippedBy, `${label}: ${stop.key} ring is not clipped`).toEqual([]);
    expect.soft(stop.inViewport, `${label}: ${stop.key} is on screen`).toBe(true);
  }
  console.log(`[keyboard] ${label}: ${stops.length} focus stops`);
  expect
    .soft(stops.length, `${label}: focus reaches every control`)
    .toBeGreaterThanOrEqual(MIN_FOCUS_STOPS[label as keyof typeof MIN_FOCUS_STOPS]);
  return stops;
}

test("keyboard focus is visible and unclipped", async ({ browser }) => {
  test.setTimeout(240_000);
  for (const width of [1440, 390]) {
    const home = await newPage(browser, marketingOrigin, width);
    await home.goto("/");
    await settle(home);
    const stops = await tabThrough(home, `marketing home at ${width}px`);
    for (const stage of ["Upload", "Compare", "Review", "Package"]) {
      expect
        .soft(
          stops.some((stop) => stop.key.startsWith("BUTTON") && stop.key.includes(stage)),
          `demo stage ${stage} is reached by keyboard`,
        )
        .toBe(true);
    }
    await home.context().close();
  }

  const login = await newPage(browser, appOrigin, 1440);
  await login.goto("/login");
  await settle(login);
  const loginStops = await tabThrough(login, "login");
  expect(loginStops.length).toBeGreaterThanOrEqual(MIN_FOCUS_STOPS.login);
  await login.context().close();

  const { storageState } = await signedIn(browser);
  const dashboard = await newPage(browser, appOrigin, 1440, storageState);
  await dashboard.goto("/dashboard");
  await expect(dashboard.locator(".metric").first()).toBeVisible();
  await settle(dashboard);
  const dashboardStops = await tabThrough(dashboard, "dashboard");
  expect(dashboardStops.length).toBeGreaterThanOrEqual(MIN_FOCUS_STOPS.dashboard);
  await dashboard.context().close();
});

test("each demo stage button changes the displayed stage", async ({ browser }) => {
  const page = await newPage(browser, marketingOrigin, 1440);
  await page.goto("/");
  await settle(page);
  const controls = page.locator(".demo-controls button");
  await expect(controls).toHaveCount(4);
  for (const [index, title] of [
    "Gather the documents",
    "See the difference",
    "Record the decision",
    "Approve the package",
  ].entries()) {
    await controls.nth(index).click();
    await expect(page.locator(".demo-body h4")).toHaveText(title);
    await expect(controls.nth(index)).toHaveAttribute("aria-pressed", "true");
  }
  await page.context().close();
});

function normalizeLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

test("home copy matches the approved list", async ({ browser }) => {
  const page = await newPage(browser, marketingOrigin, 1440);
  await page.goto("/");
  await settle(page);
  // CSS uppercases some labels. Compare the words as written in the page.
  await page.addStyleTag({ content: "* { text-transform: none !important; }" });
  const visible = await page.locator("body").innerText();
  const expected = readFileSync(
    path.resolve(__dirname, "fixtures/home-copy.txt"),
    "utf8",
  );
  expect(normalizeLines(visible)).toEqual(normalizeLines(expected));

  const promise = page.locator(".promise-copy");
  await expect(promise.locator(".promise-list li").last()).toContainText(
    "submission-ready",
  );
  const caption = promise.locator(".promise-list + .promise-caption");
  await expect(caption).toContainText("does not guarantee carrier acceptance");
  expect(visible.match(/submission-ready/gi)).toHaveLength(1);

  expect(visible).not.toMatch(/\b\d+(?:\.\d+)?\s*(?:%|percent\b|x\b|k\+|\+)/i);
  expect(visible).not.toMatch(/testimonial|trusted by|our customers|partners?\b/i);
  // No imagery anywhere on the page, header and footer included. The brand mark is
  // a text glyph and the hero media layer carries the hero background tokens.
  const allowed = ".brand, .brand *, .hero-media";
  await expect(
    page.locator(
      `:is(img, picture, svg, figure, blockquote, video, canvas, object, embed, iframe):not(${allowed})`,
    ),
  ).toHaveCount(0);
  const backgroundImages = await page.evaluate((allowedSelector) => {
    const found: string[] = [];
    for (const element of document.querySelectorAll("body *")) {
      if (element.matches(allowedSelector)) continue;
      for (const pseudo of [null, "::before", "::after"]) {
        const image = getComputedStyle(element, pseudo).backgroundImage;
        if (image.includes("url(")) {
          found.push(`${element.tagName}.${element.className}${pseudo ?? ""}`);
        }
      }
    }
    return found;
  }, allowed);
  expect(backgroundImages).toEqual([]);
  await page.context().close();
});
