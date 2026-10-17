import { readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, type Page } from "@playwright/test";

import { VERDICT_PATTERNS } from "../lib/wording";

// CC0 / public-domain fixture photos (eval/fixtures/photos/manifest.json): no real listing photos.
const FIXTURES = join(__dirname, "..", "..", "..", "eval", "fixtures", "photos", "images");

export function fixturePhoto(prefix: string): string {
  const name = readdirSync(FIXTURES).find((f) => f.startsWith(`${prefix}-`));
  if (!name) throw new Error(`no fixture photo ${prefix}`);
  return join(FIXTURES, name);
}

export type Listing = {
  text: string;
  rent: string;
  bedrooms: string;
  address: string;
  city: string;
  phone?: string;
  photos: string[];
};

export async function fillAndSubmit(page: Page, listing: Listing): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Listing text").fill(listing.text);
  await page.getByLabel("Monthly rent ($)").fill(listing.rent);
  await page.getByLabel("Bedrooms").selectOption(listing.bedrooms);
  await page.getByLabel("Address or nearest intersection").fill(listing.address);
  await page.getByLabel("City").fill(listing.city);
  if (listing.phone) await page.getByLabel("Phone number").fill(listing.phone);
  await page.getByLabel(/Choose photos/).setInputFiles(listing.photos.map(fixturePhoto));
  await expect(page.getByText(`${listing.photos.length} of 10 added`)).toBeVisible();
  await page.getByRole("button", { name: "Check this listing" }).click();
  await expect(page).toHaveURL(/\/checks\/[0-9a-f-]{36}$/);
}

export async function waitForReport(page: Page): Promise<string> {
  const heading = page.getByRole("heading", {
    level: 1,
    name: /^(Low risk|Some risk signals|High risk signals|Incomplete)$/,
  });
  await expect(heading).toBeVisible({ timeout: 90_000 });
  return (await heading.textContent()) ?? "";
}

export async function expectNoVerdictWording(page: Page): Promise<void> {
  const text = await page.locator("body").innerText();
  for (const pattern of VERDICT_PATTERNS) {
    expect(text, `matched ${pattern}`).not.toMatch(pattern);
  }
}
