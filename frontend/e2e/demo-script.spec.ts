import { expect, test } from "@playwright/test";

/**
 * The 5-minute demo script (docs/DEMO.md), beat by beat. If this passes, the live demo works.
 * Runs serially: Challenge Mode mutates the corpus and the last step resets it.
 */
test.describe.configure({ mode: "serial" });

const LATE_FEE = "What is the late payment fee on the Platinum Credit Card?";

async function ask(page: import("@playwright/test").Page, question: string) {
  await page.getByTestId("question-input").fill(question);
  await page.getByTestId("ask-button").click();
  await expect(page.getByTestId("answer-panel")).toBeVisible();
}

test("0:00 hook: Corpus Health counts the contradictions", async ({ page }) => {
  await page.goto("/health");
  const totals = page.getByTestId("health-totals");
  await expect(totals).toContainText("Contradictions");
  await expect(page.getByTestId("health-conflict").first()).toBeVisible();
  expect(await page.getByTestId("health-conflict").count()).toBeGreaterThanOrEqual(15);
  await expect(page.getByTestId("heatmap")).toBeVisible();
  await expect(page.getByTestId("suggestions")).toContainText("Retire or update");
});

test("0:30 split screen: baseline blends a stale value, TruthGuard resolves with a rule chain", async ({ page }) => {
  await page.goto("/");
  await ask(page, LATE_FEE);
  await expect(page.getByTestId("status-badge").first()).toHaveText("Conflict resolved");
  await expect(page.getByTestId("answer-text")).toContainText("₹950");
  await expect(page.getByTestId("rule-chain").first()).toContainText("Explicit supersession");
  await expect(page.getByTestId("injection-badge")).toBeVisible();
  // the naive baseline answers confidently with a stale value, flagged in red
  await expect(page.getByTestId("baseline-answer")).toContainText("₹750");
  await expect(page.getByTestId("baseline-panel")).toContainText("Stale");
  // debate theatre shows every agent and the critic's checks
  await expect(page.getByTestId("debate-theatre")).toContainText("span integrity");
  // evidence X-ray opens the amendment at the cited span
  await page.getByTestId("citation").first().click();
  await expect(page.getByTestId("xray")).toBeVisible();
  await expect(page.locator("mark.xray")).toContainText("₹950");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("trust-meter")).toBeVisible();
  await expect(page.getByTestId("cost-ticker")).toContainText("ms");
});

test("1:30 time travel: the future-dated KYC policy takes over after 1 Jan 2027", async ({ page }) => {
  await page.goto("/");
  await ask(page, "How often must high-risk customers update their KYC?");
  await expect(page.getByTestId("answer-text")).toContainText("2 years");
  await page.getByTestId("time-travel").getByTitle(/2027-01-01/).click();
  await expect(page.getByTestId("answer-text")).toContainText("1 year");
  await expect(page.getByTestId("answer-panel")).toContainText("time travel");
});

test("2:15 Challenge Mode: a judge's memo creates an unresolved conflict", async ({ page }) => {
  await page.goto("/");
  await ask(page, LATE_FEE);
  await page.getByTestId("challenge-open").click();
  await page.getByTestId("challenge-add").click();
  await expect(page.getByTestId("challenge-result")).toContainText("₹1,200");
  await page.getByTestId("challenge-reask").click();
  await expect(page.getByTestId("status-badge").first()).toHaveText("Unresolved conflict");
  await expect(page.getByTestId("conflict-card").first()).toContainText("Possible unofficial update");
  await expect(page.getByTestId("conflict-card").first()).toContainText("₹1,200");
});

test("3:15 governance: recency-over-authority flips the dispute-window verdict", async ({ page }) => {
  await page.goto("/");
  await ask(page, "How many days do customers have to report a disputed transaction?");
  await expect(page.getByTestId("status-badge").first()).toHaveText("Unresolved conflict");
  await page.goto("/policy");
  await page.getByTestId("preset-recency").click();
  await expect(page.getByTestId("cross-series")).toBeChecked();
  await page.getByTestId("try-policy").click();
  const preview = page.getByTestId("policy-preview");
  await expect(preview).toContainText("Unresolved conflict");
  await expect(preview).toContainText("Conflict resolved");
  await expect(preview).toContainText("60 days");
});

test("3:45 proof and reset", async ({ page }) => {
  await page.goto("/eval");
  await expect(page.locator("main")).toContainText(/Measured proof|No eval results yet/);
  await page.goto("/lineage");
  await expect(page.getByTestId("lineage-series").first()).toBeVisible();
  await page.goto("/");
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/v1/admin/reset") && r.ok()),
    page.getByTestId("reset-button").click(),
  ]);
  await ask(page, LATE_FEE);
  await expect(page.getByTestId("status-badge").first()).toHaveText("Conflict resolved");
});
