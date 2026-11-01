import { test, expect, type Page } from "@playwright/test";

async function agencyGet(page: Page, path: string) {
  const me = await (await page.request.get("/api/v1/me")).json();
  return page.request.get(path, { headers: { "X-Agency-Context": me.agency_context } });
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
  const submissionId = page.url().split("/").at(-1);
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
    await card.getByRole("button", { name: "Save verified value", exact: true }).click();
    await expect(page.locator(".review-card")).toHaveCount(count - 1);
    count--;
  }
  await page.getByRole("button", { name: "Package & approval", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Generate submission package" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Generate submission package" }).click();
  await expect(page.getByRole("link", { name: "Preview PDF" }).first()).toBeVisible({
    timeout: 90_000,
  });
  await expect(page.getByRole("link", { name: "Export PDF" })).toHaveCount(0);
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
  const response = await page.request.get((await exported.getAttribute("href"))!);
  expect(response.status()).toBe(200);
  expect((await response.body()).subarray(0, 4).toString()).toBe("%PDF");
  await expect(
    page.locator(".page-heading").getByText("approved", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: ".artifacts/approved-workspace.png", fullPage: true });
  await page.getByRole("button", { name: "History", exact: true }).click();
  await expect(page.getByText("package approved", { exact: true })).toBeVisible();
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
