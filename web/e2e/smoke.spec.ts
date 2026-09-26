import { expect, test } from "@playwright/test";

test("guest onboarding → brand recommendation → log a drink → my taste", async ({ page }) => {
  await page.goto("/");
  await page.waitForURL("**/onboarding");
  await page.getByRole("radio", { name: "디카페인만" }).click();
  await page.getByRole("button", { name: "다음" }).click();
  await page.getByRole("button", { name: "과일" }).click();
  await page.getByRole("button", { name: "다음" }).click();
  await page.getByRole("button", { name: "시작하기" }).click();
  await page.waitForURL((u) => u.pathname === "/");

  await page.getByRole("button", { name: "스타벅스" }).click();
  const cards = page.locator("article");
  await expect(cards).toHaveCount(3, { timeout: 30_000 });
  await expect(cards.first().getByText(/디카페인/).first()).toBeVisible();
  await expect(page.getByText("설명을 쓰는 중…")).toHaveCount(0, { timeout: 45_000 });

  await cards.first().getByRole("button", { name: "마셔봤어요" }).click();
  await page.getByRole("button", { name: "5점" }).click();
  await page.getByRole("button", { name: "저장" }).click();
  await expect(page.getByText("기록했어요")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "닫기" }).click();

  await page.getByRole("link", { name: "내 취향" }).click();
  await expect(page.getByText("기록 1회")).toBeVisible();
  await expect(page.locator("li").filter({ hasText: "★★★★★" })).toHaveCount(1);
});
