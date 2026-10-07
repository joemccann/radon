import { readdirSync } from "node:fs";
import { test } from "@e2e-dev/web";
import { expect } from "e2e";

export const publicRoutes = [
  "/", "/agent-instructions", "/convex-options-from-dark-pool-flow",
  "/crash-risk-index", "/defined-risk-options-structures",
  "/developers", "/developers/auth", "/developers/mcp",
  "/developers/openapi", "/developers/recipes", "/developers/webhooks",
  "/fractional-kelly-position-sizing", "/interactive-brokers-dark-pool-terminal",
  "/privacy", "/status", "/terms", "/unusual-whales-interactive-brokers",
];

for (const route of publicRoutes) {
  test(`public route ${route}: headings, links, responsive layout`, async ({ app, screen, browser }) => {
    await app.open(route);
    await expect(screen.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(screen.getByRole("heading", { level: 1 })).toBeVisible();
    const brokenAnchors = await browser.evaluate(() => Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href]"))
      .filter(a => a.getAttribute("href")?.startsWith("#") && a.hash.length > 1 && !document.getElementById(decodeURIComponent(a.hash.slice(1))))
      .map(a => a.getAttribute("href")));
    expect(brokenAnchors).toEqual([]);
    for (const width of [1440, 390]) {
      await browser.setViewport({ width, height: 900 });
      expect(await browser.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    }
  });
}

test("agent prompt dialog supports keyboard dismissal and restores focus", async ({ app, screen, browser }) => {
  await app.open("/crash-risk-index");
  const trigger = screen.getByRole("button", { name: "View prompt" }).first();
  await trigger.click();
  await expect(screen.getByRole("dialog", { name: "Agent prompt" })).toBeVisible();
  const close = screen.getByRole("button", { name: "Close", exact: true });
  await expect(close).toBeFocused();
  await browser.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await browser.keyboard.press("Shift+Tab");
  await expect(close).toBeFocused();
  await browser.keyboard.press("Escape");
  await expect(screen.getByRole("dialog")).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("theme survives reload and mobile menu supports links and Escape", async ({ app, screen, browser }) => {
  await app.open("/");
  await screen.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(browser.locator("html")).toHaveAttribute("data-theme", "dark");
  await browser.reload();
  await expect(browser.locator("html")).toHaveAttribute("data-theme", "dark");
  await screen.getByRole("button", { name: "Switch to light mode" }).click();
  await browser.setViewport({ width: 390, height: 844 });
  await screen.getByRole("button", { name: "Open more navigation" }).click();
  await expect(screen.getByRole("navigation", { name: "Overflow navigation" })).toBeVisible();
  await browser.keyboard.press("Escape");
  await expect(screen.getByRole("navigation", { name: "Overflow navigation" })).toBeHidden();
  await screen.getByRole("button", { name: "Open more navigation" }).click();
  await screen.getByRole("navigation", { name: "Overflow navigation" }).getByRole("link", { name: "FAQ" }).click();
  await expect(browser).toHaveURL(/#faq$/);
  await expect(screen.getByRole("navigation", { name: "Overflow navigation" })).toBeHidden();
  await expect(screen.getByRole("link", { name: "Try the demo", exact: true }).first()).toHaveAttribute("href", "https://demo.radon.run/sign-up");
});

test("404 recovery links return to public documents", async ({ app, screen, browser }) => {
  await app.open("/missing-tester-army-page");
  await expect(screen.getByRole("heading", { name: "Nothing at this address." })).toBeVisible();
  await screen.getByRole("link", { name: "Back to the front page" }).click();
  await expect(browser).toHaveURL("/");
  await expect(screen.getByRole("heading", { level: 1 })).toBeVisible();
});

test("homepage header anchors and footer document journeys", async ({ app, screen, browser }) => {
  await app.open("/");
  for (const label of ["View", "Expression", "Discipline", "Surfaces", "FAQ"]) {
    await screen.getByRole("navigation", { name: "Primary navigation", exact: true }).getByRole("link", { name: label, exact: true }).click();
    await expect(browser).toHaveURL(new RegExp(`#${label.toLowerCase()}$`));
  }
  for (const label of ["Privacy", "Terms", "Developers"]) {
    await app.open("/");
    await screen.getByRole("link", { name: label, exact: true }).click();
    await expect(browser).toHaveURL(`/${label.toLowerCase()}`);
    await expect(screen.getByRole("heading", { level: 1 })).toBeVisible();
    await screen.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Radon", exact: true }).click();
    await expect(browser).toHaveURL("/");
  }
});

test("seven recipes expose complete prompts and clipboard feedback", async ({ app, screen, browser }) => {
  await app.open("/developers/recipes");
  await expect(screen.getByRole("button", { name: "Copy agent prompt" })).toHaveCount(7);
  // Deterministic browser-level clipboard stub verifies actual UI payload without permission prompts.
  await browser.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: async (text: string) => { sessionStorage.setItem("copied-prompt", text); },
    } });
    return true;
  });
  for (let index = 0; index < 7; index++) {
    await screen.getByRole("button", { name: "Copy agent prompt" }).nth(index).click();
    await expect(screen.getByRole("status").nth(index)).toHaveText(/copied/i);
    const text = await browser.evaluate(() => sessionStorage.getItem("copied-prompt"));
    for (const heading of ["When to use", "Hard nos", "How to call", "Parameters / constraints", "Definition of done for the agent"]) {
      expect(text).toContain(`## ${heading}`);
    }
    await screen.getByRole("button", { name: "View prompt" }).nth(index).click();
    await expect(screen.getByRole("dialog")).toContainText(text!);
    await screen.getByRole("button", { name: "Close", exact: true }).click();
    await expect(screen.getByRole("dialog")).toBeHidden();
  }
  await browser.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { throw new Error("Permission denied"); } } });
    document.execCommand = () => false;
    return true;
  });
  await screen.getByRole("button", { name: "Copy agent prompt" }).first().click();
  await expect(screen.getByRole("status").first()).toHaveText(/copy failed/i);
});

test("public document contents links navigate to their sections", async ({ app, screen, browser }) => {
  for (const path of publicRoutes.filter(path => path.startsWith("/developers") || ["/agent-instructions", "/privacy", "/terms"].includes(path))) {
    await app.open(path);
    // Repeated TOC clicks scroll back to the navigation between targets. Keep
    // that movement immediate so the next link remains actionable throughout
    // the journey; the href, hash, and target visibility are still exercised.
    await browser.evaluate(() => { document.documentElement.style.scrollBehavior = "auto"; return true; });
    const contents = await browser.evaluate(() => Array.from(document.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Contents"] a')).map(a => ({ text: a.textContent!.trim(), href: a.getAttribute("href")! })));
    expect(contents.length).toBeGreaterThan(0);
    for (const item of contents) {
      await screen.getByRole("navigation", { name: "Contents" }).getByRole("link", { name: item.text, exact: true }).click();
      await expect(browser).toHaveURL(new RegExp(`${item.href}$`));
      await expect(browser.locator(item.href)).toBeVisible();
    }
  }
});

test("local links and public machine-readable endpoints respond", async ({ app, browser }) => {
  const paths = new Set(publicRoutes);
  for (const path of publicRoutes) {
    await app.open(path);
    const hrefs = await browser.evaluate(() => Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href]")).map(a => a.getAttribute("href")!));
    for (const href of hrefs) {
      if (href.startsWith("/") && !href.startsWith("//")) paths.add(href.split("#")[0] || "/");
      if (href.startsWith("https://demo.radon.run")) expect(new URL(href).protocol).toBe("https:");
    }
  }
  for (const path of [...paths, "/robots.txt", "/sitemap.xml", "/manifest.webmanifest", "/llms.txt", "/openapi.json"]) {
    const response = await fetch(`http://127.0.0.1:3342${path}`);
    expect(response.status).toBe(200);
  }
  for (const path of ["/", "/developers", "/developers/recipes", "/crash-risk-index"]) {
    const response = await fetch(`http://127.0.0.1:3342${path}`, { headers: { Accept: "text/markdown" } });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/markdown");
    expect(await response.text()).toContain("Radon");
  }
  expect((await fetch("http://127.0.0.1:3342/missing.md")).status).toBe(404);
});


test("status public demo link opens the signup entry", async ({ app, screen }) => {
  await app.open("/status");
  await expect(screen.getByRole("link", { name: /^demo\.radon\.run/ })).toHaveAttribute("href", "https://demo.radon.run/sign-up");
});

test("homepage dossier links open all six product documents", async ({ app, screen, browser }) => {
  for (const path of publicRoutes.filter(path => ["/convex-options-from-dark-pool-flow", "/crash-risk-index", "/defined-risk-options-structures", "/fractional-kelly-position-sizing", "/interactive-brokers-dark-pool-terminal", "/unusual-whales-interactive-brokers"].includes(path))) {
    await app.open("/");
    await browser.locator(`a[href="${path}"]`).first().click();
    await expect(browser).toHaveURL(path);
    await expect(screen.getByRole("heading", { level: 1 })).toBeVisible();
  }
});


test("public inventory matches every current App Router page", async () => {
  const pageFiles = readdirSync(new URL("../../app/", import.meta.url), { recursive: true })
    .filter(path => typeof path === "string" && /(^|\/)page\.tsx$/.test(path)) as string[];
  const routes = pageFiles.map(path => path === "page.tsx" ? "/" : `/${path.replace(/\/page\.tsx$/, "")}`);
  expect(routes.sort()).toEqual([...publicRoutes].sort());
});
