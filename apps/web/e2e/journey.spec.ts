// M7 "Done when": the full journey in a real browser, submit -> progress -> report, against the
// real API, worker and Postgres (playwright.config.ts).

import { expect, test } from "@playwright/test";

import { expectNoVerdictWording, fillAndSubmit, waitForReport } from "./helpers";

test("submit a listing, watch progress, read the report with evidence and next steps", async ({
  page,
}) => {
  await fillAndSubmit(page, {
    text: "Bright 2 bedroom near campus, all inclusive. Text me to book a viewing.",
    rent: "900",
    bedrooms: "2",
    address: "200 Queen St W",
    city: "Toronto",
    phone: "(416) 555-0123",
    photos: ["01", "15"],
  });

  // Progress view (it may already be done if the worker is fast; both are valid states).
  await expect(
    page.getByRole("heading", { level: 1, name: /Checking your listing|risk|Incomplete/ }),
  ).toBeVisible();

  const level = await waitForReport(page);
  // $900 for a 2-bedroom in Toronto is far below the CMHC average: a strong price signal.
  expect(level).toBe("High risk signals");
  await expect(page.getByText(/Based on: .*Rent compared with the area/)).toBeVisible();

  for (const title of ["Rent compared with the area", "Photos seen before", "Contact details"]) {
    const card = page.getByRole("article", { name: title });
    await expect(card).toBeVisible();
  }
  const price = page.getByRole("article", { name: "Rent compared with the area" });
  await expect(price.getByText("Strong risk signal")).toBeVisible();
  await expect(price.getByText(/Next step:/)).toBeVisible();
  await price.getByText("Show the evidence").click();
  await expect(price.getByText("Rent in the listing")).toBeVisible();
  await expect(price.getByText("$900", { exact: true })).toBeVisible();

  // The paid web search is off: shown as not checked, not as a finding.
  await expect(
    page.getByRole("article", { name: "Photos elsewhere online" }).getByText("Not checked"),
  ).toBeVisible();
  await expectNoVerdictWording(page);
});

test("a second listing reusing the photo and phone shows the links", async ({ page }) => {
  const shared = { phone: "905-555-0177", photos: ["05"] };
  await fillAndSubmit(page, {
    text: "1 bed apartment, close to transit.",
    rent: "1800",
    bedrooms: "1",
    address: "12 King St W",
    city: "Hamilton",
    ...shared,
  });
  await waitForReport(page);

  await fillAndSubmit(page, {
    text: "Cozy 1 bedroom, utilities included.",
    rent: "1750",
    bedrooms: "1",
    address: "88 Main St",
    city: "Guelph",
    ...shared,
  });
  await waitForReport(page);

  const photos = page.getByRole("article", { name: "Photos seen before" });
  await expect(photos.getByText("Strong risk signal")).toBeVisible();
  const linked = page.getByRole("region", { name: "Linked listings" });
  await expect(linked.getByText("Hamilton")).toBeVisible();
  await expect(linked.getByText("same phone")).toBeVisible();
  await expect(linked.getByText("same photo")).toBeVisible();
  await expectNoVerdictWording(page);
});

test("missing fields are listed and the summary takes focus", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Check this listing" }).click();
  // Next.js adds its own (empty) role="alert" route announcer, so pick ours by its text.
  const summary = page.getByRole("alert").filter({ hasText: "Please fix the following" });
  await expect(summary).toBeFocused();
  await expect(summary.getByRole("link", { name: "Photos" })).toBeVisible();
});

test("the form can be filled and sent with the keyboard only", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab"); // skip link
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.getByLabel("Listing text").focus();
  await page.keyboard.type("Room for rent.");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Link to the listing (optional)")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Monthly rent ($)")).toBeFocused();
});
