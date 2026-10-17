// @screenshots: images of the form, progress view and report for docs/screenshots, at a phone and a
// desktop width. Run on request (npm run e2e:screenshots), not in CI. Same backend as the journey
// test, so only the offline signals run (price, photos, contact); text and address are not shown.

import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { fillAndSubmit, fixturePhoto, waitForReport } from "./helpers";

const OUT = join(__dirname, "..", "..", "..", "docs", "screenshots");
// Each size uses its own photos, phone and cities, so the two runs never link to each other.
const SIZES = [
  {
    name: "mobile",
    width: 390,
    height: 844,
    phone: "905-555-0191",
    earlierCity: "Hamilton",
    city: "Toronto",
    photos: ["05", "06", "16", "17", "19", "20"],
  },
  {
    name: "desktop",
    width: 1280,
    height: 900,
    phone: "905-555-0192",
    earlierCity: "Oakville",
    city: "Mississauga",
    photos: ["12", "07", "13", "18", "21", "22"],
  },
] as const;

async function shot(page: Page, file: string, fullPage = true): Promise<void> {
  await page.screenshot({ path: join(OUT, file), fullPage, type: "jpeg", quality: 80 });
}

for (const size of SIZES) {
  test(`@screenshots ${size.name}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    const { phone } = size;

    // An earlier listing, so the report below has a reused photo and phone to show.
    await fillAndSubmit(page, {
      text: "Bright 1 bedroom close to transit and campus.",
      rent: "1850",
      bedrooms: "1",
      address: "12 King St W",
      city: size.earlierCity,
      phone,
      photos: [size.photos[0]],
    });
    await waitForReport(page);

    // The form, filled in, with photo previews.
    await page.goto("/");
    await page.getByLabel("Listing text").fill(
      "Furnished 1 bedroom, all inclusive, available now. Text me to book a viewing.",
    );
    await page.getByLabel("Monthly rent ($)").fill("950");
    await page.getByLabel("Bedrooms").selectOption("1");
    await page.getByLabel("Address or nearest intersection").fill("88 Main St");
    await page.getByLabel("City").fill(size.city);
    await page.getByLabel("Phone number").fill(phone);
    await page
      .getByLabel(/Choose photos/)
      .setInputFiles(size.photos.map(fixturePhoto));
    await expect(page.getByText("6 of 10 added")).toBeVisible();
    await shot(page, `form-${size.name}.jpg`);

    // Progress: captured as soon as it shows (the worker may finish within a second or two).
    await page.getByRole("button", { name: "Check this listing" }).click();
    const progress = page.getByRole("heading", { level: 1, name: "Checking your listing" });
    const report = page.getByRole("heading", { level: 1, name: /risk|Incomplete/ });
    await expect(progress.or(report)).toBeVisible({ timeout: 30_000 });
    if (await progress.isVisible()) await shot(page, `progress-${size.name}.jpg`, false);
    else test.info().annotations.push({ type: "note", description: "progress view not caught" });

    await waitForReport(page);
    await page
      .getByRole("article", { name: "Rent compared with the area" })
      .getByText("Show the evidence")
      .click();
    await shot(page, `report-${size.name}.jpg`);
  });
}
