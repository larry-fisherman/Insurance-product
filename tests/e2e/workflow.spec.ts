import { readFileSync } from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const axeSource = readFileSync(
  path.resolve(__dirname, "../../node_modules/axe-core/axe.min.js"),
  "utf8",
);

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

// Package & approval evidence, taken between measured stages: a screenshot at
// 1440px and 390px from the top of the page with the pointer off every control,
// plus axe color-contrast and horizontal-overflow checks at both widths. The
// default 1440px viewport is restored afterwards.
async function reviewPackageState(page: Page, state: string) {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await parkPointer(page);
    await page.screenshot({
      path:
        ".artifacts/visual-review/package-approval-" +
        state +
        (width === 390 ? "-390" : "") +
        ".png",
      fullPage: true,
    });
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect
      .soft(overflow.scrollWidth, `Package & approval ${state} at ${width}px overflow`)
      .toBeLessThanOrEqual(overflow.innerWidth);
    await page.evaluate(axeSource);
    const contrast = await page.evaluate(async () => {
      const axe = (window as unknown as { axe: any }).axe;
      const results = await axe.run(document, {
        runOnly: { type: "rule", values: ["color-contrast"] },
      });
      return {
        passes: results.passes.flatMap((rule: any) => rule.nodes).length,
        violations: results.violations.flatMap((rule: any) =>
          rule.nodes.map((node: any) => ({
            target: node.target.join(" "),
            summary: node.failureSummary,
          })),
        ),
      };
    });
    expect
      .soft(contrast.violations, `Package & approval ${state} at ${width}px contrast`)
      .toEqual([]);
    expect
      .soft(contrast.passes, `Package & approval ${state} at ${width}px judged text`)
      .toBeGreaterThan(0);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}

async function agencyGet(page: Page, path: string) {
  const me = await (await page.request.get("/api/v1/me")).json();
  return page.request.get(path, { headers: { "X-Agency-Context": me.agency_context } });
}

async function measuredStage<T>(
  name: string,
  timeout: number,
  action: () => Promise<T>,
): Promise<T> {
  const started = performance.now();
  try {
    return await test.step(name, action, { timeout });
  } finally {
    const durationMs = Math.round(performance.now() - started);
    test.info().annotations.push({
      type: "stage-duration",
      description: `${name}: ${durationMs} ms (budget ${timeout} ms)`,
    });
    console.log(`[workflow stage] ${name}: ${durationMs} ms / ${timeout} ms`);
  }
}

test.beforeAll(async ({ request }) => {
  await expect
    .poll(
      async () => {
        try {
          return (await request.get("/api/v1/health/ready", { timeout: 3000 })).ok();
        } catch {
          return false;
        }
      },
      { timeout: 90_000 },
    )
    .toBe(true);
});

test("fictional brokerage reviews evidence, approves exact files, and exports", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // Independent stage budgets expose latency failures; other tests keep the
  // suite default. The previous 120-second whole-case budget competed with
  // several individual 90-second waits (audit A11).
  // Budgets allow headroom above the measured November 2 local run:
  // approximately 6 / 20 / 31 / 7 / 2 / 2 seconds respectively.
  const budgets = {
    open: 20_000,
    processing: 60_000,
    review: 60_000,
    generation: 30_000,
    approval: 15_000,
    export: 15_000,
  };
  test.setTimeout(Object.values(budgets).reduce((total, value) => total + value, 0));
  const submissionId = await measuredStage(
    "Open fictional workspace",
    budgets.open,
    async () => {
      await page.goto("/login");
      await page.getByRole("button", { name: "Open a fictional example" }).click();
      await expect(page).toHaveURL(/submissions\/[0-9a-f-]+/, { timeout: 90_000 });
      await expect(
        page.getByRole("heading", {
          name: "Lakeshore Design Services LLC",
          exact: true,
          level: 1,
        }),
      ).toBeVisible();
      return page.url().split("/").at(-1);
    },
  );
  await measuredStage("Process four source documents", budgets.processing, async () => {
    await expect
      .poll(
        async () => {
          const response = await agencyGet(page, "/api/v1/submissions/" + submissionId);
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
  });
  await measuredStage("Review evidence and reconcile facts", budgets.review, async () => {
    await page.getByRole("button", { name: /Data & discrepancies/ }).click();
    await expect(
      page.getByText("Local parser · requires broker verification").first(),
    ).toBeVisible({ timeout: 90_000 });
    // The UI offers choices rather than silently selecting a conflicting estimate.
    const sales = page
      .locator(".review-card")
      .filter({ has: page.getByRole("heading", { name: "Exposure", exact: true }) });
    await expect(sales.getByText("conflict", { exact: true })).toBeVisible();
    await expect(sales.locator("input[type=radio]:checked")).toHaveCount(0);
    await page.getByText("View cited PDF page", { exact: true }).first().click();
    await expect(page.locator("iframe[title^='Cited page']").first()).toBeVisible();
    const citedPdf = await page
      .locator("iframe[title^='Cited page']")
      .first()
      .getAttribute("src");
    expect(citedPdf).toContain("inline=true");
    expect(citedPdf).toContain("agency_context=");
    expect(citedPdf).toContain("#page=");
    const citedResponse = await page.request.get(citedPdf!.split("#")[0]);
    expect(citedResponse.headers()["content-disposition"]).toContain("inline");
    await page.getByText("View cited spreadsheet cells", { exact: true }).first().click();
    await expect(
      page.getByRole("columnheader", { name: "Cell", exact: true }).first(),
    ).toBeVisible();
    let count = await page.locator(".review-card").count();
    for (let step = 0; step < 50 && count > 0; step++) {
      const card = page.locator(".review-card").first();
      await card.getByRole("radio").first().check();
      await card
        .getByRole("textbox", { name: /Review note|Resolution reason/ })
        .fill(
          "Synthetic browser test: source reviewed; selected estimate confirmed by the fictional insured.",
        );
      await card
        .getByRole("button", { name: "Save verified value", exact: true })
        .click();
      await expect(page.locator(".review-card")).toHaveCount(count - 1);
      count--;
    }
  });
  await measuredStage("Generate candidate package", budgets.generation, async () => {
    await page.getByRole("button", { name: "Package & approval", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Generate submission package" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Generate submission package" }).click();
    await expect(page.getByRole("link", { name: "Preview PDF" }).first()).toBeVisible({
      timeout: 90_000,
    });
    await expect(page.getByRole("link", { name: "Export PDF" })).toHaveCount(0);
  });
  await reviewPackageState(page, "before-approval");
  const approvedBytes = await measuredStage(
    "Approve and inspect the exact PDF",
    budgets.approval,
    async () => {
      await page
        .getByRole("checkbox", {
          name: "I reviewed the candidate files, source evidence, and documented exceptions.",
        })
        .check();
      await page
        .getByRole("textbox", { name: "Final approval attestation" })
        .fill(
          "I reviewed these exact fictional submission files and documented exceptions and approve the synthetic package for test export.",
        );
      await page.getByRole("button", { name: "Approve this exact package" }).click();
      const exported = page.getByRole("link", { name: "Export PDF" }).first();
      await expect(exported).toBeVisible();
      await expect(
        page.locator(".page-heading").getByText("approved", { exact: true }),
      ).toBeVisible();
      const preview = await page.request.get(
        (await page
          .getByRole("link", { name: "Preview PDF" })
          .first()
          .getAttribute("href"))!,
      );
      expect(preview.status()).toBe(200);
      const bytes = await preview.body();
      expect(bytes.subarray(0, 4).toString()).toBe("%PDF");
      return bytes;
    },
  );
  await reviewPackageState(page, "after-approval");
  await measuredStage(
    "Export identical bytes and verify history",
    budgets.export,
    async () => {
      const response = await page.request.get(
        (await page
          .getByRole("link", { name: "Export PDF" })
          .first()
          .getAttribute("href"))!,
      );
      expect(response.status()).toBe(200);
      expect(await response.body()).toEqual(approvedBytes);
      await expect(
        page.locator(".page-heading").getByText("exported", { exact: true }),
      ).toBeVisible();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: ".artifacts/exported-workspace.png",
        fullPage: true,
      });
      await page.getByRole("button", { name: "History", exact: true }).click();
      await expect(page.getByText("package approved", { exact: true })).toBeVisible();
      await expect(page.getByText("package exported", { exact: true })).toBeVisible();
    },
  );
  expect(errors).toEqual([]);
});

test("mobile navigation and fictional-mode boundaries are visible", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(
    page.getByText("Synthetic review environment", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
});

test("normal PDF upload is scanned and processed without a synthetic signature", async ({
  page,
}) => {
  test.skip(
    process.env.CGL_SCAN_ACCEPTANCE !== "1",
    "Enable only with the local ClamAV service running.",
  );
  await page.goto("/login");
  await page.getByRole("button", { name: "Open a fictional example" }).click();
  await expect(page).toHaveURL(/submissions\/[0-9a-f-]+/, { timeout: 90_000 });
  const submissionId = page.url().split("/").at(-1);
  let originalId = "";
  await expect
    .poll(
      async () => {
        const response = await agencyGet(page, "/api/v1/submissions/" + submissionId);
        if (!response.ok()) return false;
        const data = await response.json();
        originalId = data.documents.find(
          (doc: { filename: string }) => doc.filename === "CGL-application.pdf",
        ).id;
        return data.jobs.every((job: { status: string }) => job.status === "completed");
      },
      { timeout: 90_000 },
    )
    .toBe(true);
  const original = await agencyGet(page, "/api/v1/documents/" + originalId + "/content");
  expect(original.status()).toBe(200);
  await page.getByRole("button", { name: "Documents", exact: true }).click();
  await page.locator("input[type=file]").setInputFiles({
    name: "Manually-uploaded-fictional-application.pdf",
    mimeType: "application/pdf",
    buffer: await original.body(),
  });
  await page.getByRole("button", { name: "Upload and process", exact: true }).click();
  await expect
    .poll(
      async () => {
        const response = await agencyGet(page, "/api/v1/submissions/" + submissionId);
        const data = await response.json();
        const doc = data.documents.find(
          (d: { filename: string }) =>
            d.filename === "Manually-uploaded-fictional-application.pdf",
        );
        return doc?.scan_status === "clean" && doc?.processing_status === "completed";
      },
      { timeout: 90_000 },
    )
    .toBe(true);
});

test("public marketing saves a fictional inquiry and cannot proxy operations", async ({
  page,
}) => {
  const origin = process.env.CGL_MARKETING_ORIGIN ?? "http://127.0.0.1:3001";
  await page.goto(origin);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Bring clarity");
  expect((await page.request.get(origin + "/api/v1/me")).status()).toBe(404);
  expect((await page.request.get(origin + "/submissions")).status()).toBe(404);
  await page.goto(origin + "/request-demo");
  await page.getByLabel("Your name").fill("Fictional Broker");
  await page.getByLabel("Work email").fill("browser-test@example.invalid");
  await page.getByLabel("Agency name").fill("Fictional Browser Agency");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Request a demo" }).click();
  await expect(page).toHaveURL(origin + "/thank-you");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(origin);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: ".artifacts/marketing-mobile.png", fullPage: true });
});
