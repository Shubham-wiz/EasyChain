import { expect, type Locator, type Page } from "@playwright/test";

export const FAKE_URL = `http://127.0.0.1:${process.env.E2E_FAKE_PORT ?? 8124}`;

export function step(page: Page, id: string): Locator {
  return page.getByTestId(`step-${id}`);
}

/** Drag a step type from the library onto the canvas at (x, y) relative to the canvas. */
export async function dragFromLibrary(page: Page, type: string, x: number, y: number) {
  const canvas = page.getByTestId("canvas");
  await page.getByTestId(`library-${type}`).dragTo(canvas, { targetPosition: { x, y } });
}

/** Connect two steps by dragging from the source handle to the target handle. */
export async function connect(page: Page, from: string, to: string, exit?: string) {
  const source = exit
    ? step(page, from).locator(`.react-flow__handle[data-handleid="exit:${exit}"]`)
    : step(page, from).locator(".react-flow__handle.source");
  const target = step(page, to).locator(".react-flow__handle.target");
  const a = await source.boundingBox();
  const b = await target.boundingBox();
  if (!a || !b) throw new Error(`Handles not visible for ${from} -> ${to}`);
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 8 });
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
}

/** Select a step on the canvas and open the Inspect tab. */
export async function inspect(page: Page, id: string) {
  await step(page, id).click({ position: { x: 60, y: 14 } });
  await page.getByRole("tab", { name: "Inspect" }).click();
}

export async function setField(page: Page, label: string, value: string) {
  const field = page.getByLabel(label, { exact: true });
  await field.fill(value);
  await field.blur();
}

export async function expectNoProblems(page: Page) {
  await expect(page.getByTestId("problems-button")).toContainText("No problems");
}
