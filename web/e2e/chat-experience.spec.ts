import { expect, test, type Page, type Route } from "@playwright/test";

type AssistantRequest = {
  model?: string;
  messages: Array<{ role: string; content: string }>;
};

const REPLY = "The flow evidence is mixed. Check the latest source timestamps before acting.";
const SAFE_ERROR = "The assistant couldn't complete this turn. No order was placed. Try again or choose another model.";

function assistantBody(content: string) {
  return `event: start\ndata: {}\n\nevent: done\ndata: ${JSON.stringify({
    content,
    model: "fixture-model",
    toolEvents: [],
  })}\n\n`;
}

async function reply(route: Route, content = REPLY) {
  await route.fulfill({ status: 200, contentType: "text/event-stream", body: assistantBody(content) });
}

/** All API traffic is intercepted; these checks cannot place a real order. */
async function installFixtures(page: Page) {
  const assistantRequests: AssistantRequest[] = [];
  const mutations: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET" && /^\/api\/(assistant|pi|orders\/(place|cancel|modify))$/.test(path)) {
      mutations.push(path);
    }
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/assistant") {
      assistantRequests.push(request.postDataJSON() as AssistantRequest);
      await reply(route);
      return;
    }
    const payloads: Record<string, unknown> = {
      "/api/portfolio": { positions: [], exposure: {}, violations: [], account_summary: null },
      "/api/orders": { open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 },
      "/api/watchlist": { watchlist: [] },
      "/api/service-health": { services: [] },
      "/api/profile": { username: "Operator", avatar_url: null },
      "/api/alerts": { alerts: [], rules: [] },
      "/api/flex-token": { remaining: 240 },
      "/api/previous-close": { closes: {} },
      "/api/models": {
        models: [
          { id: "fixture-model", provider: "openai", label: "Fixture model", refreshedAt: "2026-09-08" },
          { id: "alternative-model", provider: "anthropic", label: "Alternative model", refreshedAt: "2026-09-08" },
        ],
        defaultId: "fixture-model",
      },
    };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payloads[path] ?? {}) });
  });
  return { assistantRequests, mutations };
}

async function openChat(page: Page) {
  await expect(page.getByTestId("chat-launcher-ready")).toBeAttached({ timeout: 30_000 });
  await page.keyboard.press("ControlOrMeta+j");
  const dialog = page.getByRole("dialog", { name: "Radon chat" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Radon AI", exact: true })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "Ask Radon" })).toBeFocused();
  return dialog;
}

test("starters are editable drafts and closing preserves the conversation and draft", async ({ page }) => {
  const { assistantRequests, mutations } = await installFixtures(page);
  await page.goto("/alerts");
  const opener = page.locator("a:visible").first();
  await opener.focus();
  const dialog = await openChat(page);
  const composer = dialog.getByRole("textbox", { name: "Ask Radon" });
  await dialog.getByRole("button", { name: /Review portfolio risk/ }).click();
  await expect(composer).not.toHaveValue("");
  await expect(composer).toBeFocused();
  const starterPrompt = await composer.inputValue();
  expect(starterPrompt).toContain("portfolio risk");
  expect(mutations).toEqual([]);

  await expect(dialog.getByRole("combobox", { name: "Model" })).toHaveValue("fixture-model");
  await composer.press("Enter");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
  await expect(dialog.getByTestId("chat-messages")).toHaveAttribute("aria-busy", "false");
  expect(assistantRequests).toHaveLength(1);
  expect(assistantRequests[0].model).toBe("fixture-model");
  expect(assistantRequests[0].messages).toEqual([{ role: "user", content: starterPrompt }]);
  await composer.fill("Which evidence should I inspect next?");
  await dialog.getByRole("button", { name: "Close chat", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();

  await openChat(page);
  await expect(composer).toHaveValue("Which evidence should I inspect next?");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
  expect(assistantRequests).toHaveLength(1);
  expect(mutations).toEqual(["/api/assistant"]);
});

test("New conversation clears the transcript and draft before the next request", async ({ page }) => {
  const { assistantRequests, mutations } = await installFixtures(page);
  await page.goto("/alerts");
  const dialog = await openChat(page);
  const composer = dialog.getByRole("textbox", { name: "Ask Radon" });
  await composer.fill("Explain the latest flow evidence");
  await composer.press("Enter");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
  await expect(dialog.getByTestId("chat-messages")).toHaveAttribute("aria-busy", "false");
  await composer.fill("An unsent follow-up");
  await dialog.getByRole("button", { name: "New conversation", exact: true }).click();
  await expect(dialog.getByTestId("chat-message-assistant")).toHaveCount(0);
  await expect(dialog.getByTestId("chat-message-user")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: /Review portfolio risk/ })).toBeVisible();
  await expect(composer).toHaveValue("");
  await expect(composer).toBeFocused();
  expect(assistantRequests).toHaveLength(1);
  await composer.fill("Explain current volatility evidence");
  await composer.press("Enter");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
  expect(assistantRequests).toHaveLength(2);
  expect(assistantRequests[1].messages).toEqual([{ role: "user", content: "Explain current volatility evidence" }]);
  expect(mutations).toEqual(["/api/assistant", "/api/assistant"]);
});

test("keyboard focus remains in the dialog and Escape returns it to the opener", async ({ page }) => {
  await installFixtures(page);
  await page.goto("/alerts");
  const opener = page.locator("a:visible").first();
  await opener.focus();
  const dialog = await openChat(page);
  const controls = dialog.locator("button:visible:not([disabled]), textarea:visible, select:visible, a[href]:visible");
  const first = controls.first();
  const last = controls.last();
  await last.focus();
  await page.keyboard.press("Tab");
  await expect(first).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(last).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("Shift+Enter adds a line while Enter sends exactly one complete prompt", async ({ page }) => {
  const { assistantRequests } = await installFixtures(page);
  await page.goto("/alerts");
  const dialog = await openChat(page);
  const composer = dialog.getByRole("textbox", { name: "Ask Radon" });
  await composer.fill("Compare the flow evidence.");
  await composer.press("Shift+Enter");
  await composer.pressSequentially("Include source freshness.");
  await expect(composer).toHaveValue("Compare the flow evidence.\nInclude source freshness.");
  expect(assistantRequests).toHaveLength(0);
  await composer.press("Enter");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
  expect(assistantRequests).toHaveLength(1);
  expect(assistantRequests[0].messages.at(-1)?.content).toBe("Compare the flow evidence.\nInclude source freshness.");
});

test("Stop response settles immediately and a late result cannot overwrite a newer turn", async ({ page }) => {
  const { mutations } = await installFixtures(page);
  let releaseFirst!: () => void;
  let finishFirst!: () => void;
  const firstHeld = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const firstFinished = new Promise<void>((resolve) => { finishFirst = resolve; });
  let requestCount = 0;
  await page.route("**/api/assistant", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await firstHeld;
      try { await reply(route, "Discard this late result."); } catch { /* The stopped fetch may already be aborted. */ }
      finally { finishFirst(); }
      return;
    }
    await reply(route, "The newer answer is ready.");
  });
  await page.goto("/alerts");
  const dialog = await openChat(page);
  const composer = dialog.getByRole("textbox", { name: "Ask Radon" });
  await composer.fill("Explain the latest flow evidence");
  await composer.press("Enter");
  await expect.poll(() => requestCount).toBe(1);
  await dialog.getByRole("button", { name: "Stop response", exact: true }).click();
  await expect(dialog.getByTestId("chat-messages")).toHaveAttribute("aria-busy", "false");
  await composer.fill("Explain the current volatility evidence");
  await composer.press("Enter");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText("The newer answer is ready.");
  releaseFirst();
  await firstFinished;
  await expect(dialog.getByTestId("chat-messages")).toHaveAttribute("aria-busy", "false");
  await expect(dialog).not.toContainText("Discard this late result.");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText("The newer answer is ready.");
  expect(mutations).toEqual(["/api/assistant", "/api/assistant"]);
});

test("failed turns offer edit and retry without repeating provider internals or failed history", async ({ page }) => {
  const { mutations } = await installFixtures(page);
  const requests: AssistantRequest[] = [];
  await page.route("**/api/assistant", async (route) => {
    requests.push(route.request().postDataJSON() as AssistantRequest);
    if (requests.length === 1) {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: start\ndata: {}\n\nevent: error\ndata: {"error":"provider internal unsupported_parameter"}\n\n',
      });
      return;
    }
    await reply(route);
  });
  await page.goto("/alerts");
  const dialog = await openChat(page);
  const composer = dialog.getByRole("textbox", { name: "Ask Radon" });
  const prompt = "Explain the latest flow evidence";
  await composer.fill(prompt);
  await composer.press("Enter");
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(SAFE_ERROR);
  await expect(dialog).not.toContainText("unsupported_parameter");
  await dialog.getByRole("button", { name: "Edit prompt", exact: true }).click();
  await expect(composer).toHaveValue(prompt);
  await expect(composer).toBeFocused();
  expect(requests).toHaveLength(1);
  await dialog.getByRole("combobox", { name: "Model" }).selectOption("alternative-model");
  await dialog.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
  expect(requests).toHaveLength(2);
  expect(requests[1].model).toBe("alternative-model");
  expect(requests[1].messages).toEqual([{ role: "user", content: prompt }]);
  expect(mutations).toEqual(["/api/assistant", "/api/assistant"]);
});

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 393, height: 852 }]) {
    const size = viewport.width === 393 ? "mobile" : "desktop";
    test(`${size} ${theme} chat fits the viewport with usable composer and screenshot evidence`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await page.addInitScript((theme) => localStorage.setItem("theme", theme), theme);
      await installFixtures(page);
      await page.goto("/alerts");
      const dialog = await openChat(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const panel = dialog.getByTestId("chat-launcher-panel");
      const bounds = await panel.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.y).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width + 1);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport.height + 1);
      expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`chat-experience-${size}-${theme}-welcome.png`) });
      await dialog.getByRole("textbox", { name: "Ask Radon" }).fill("Explain the latest flow evidence");
      await dialog.getByRole("button", { name: "Send", exact: true }).click();
      await expect(dialog.getByTestId("chat-message-assistant").last()).toContainText(REPLY);
      await expect(dialog.getByTestId("chat-messages")).toHaveAttribute("aria-busy", "false");
      await expect(dialog.getByRole("textbox", { name: "Ask Radon" })).toBeInViewport();
      await expect(dialog.getByRole("button", { name: "Close chat", exact: true })).toBeInViewport();
      expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`chat-experience-${size}-${theme}-conversation.png`) });
    });
  }
}
