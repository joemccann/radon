import { expect, test } from "../../web/node_modules/@playwright/test";

test.describe("AI agent readability", () => {
  test("advertises Markdown and links machine resources from the homepage", async ({
    page,
  }) => {
    await page.goto("/");

    await expect(
      page.locator('link[rel="alternate"][type="text/markdown"]'),
    ).toHaveAttribute("href", /\/index\.md$/);

    const footer = page.locator("footer");
    for (const href of [
      "/llms.txt",
      "/openapi.json",
      "/agent-instructions",
      "/sitemap.xml",
    ]) {
      await expect(footer.locator(`a[href="${href}"]`)).toBeVisible();
    }
  });

  test("advertises Markdown alternates on dossier and developer pages", async ({
    page,
  }) => {
    for (const [pathname, markdownPath] of [
      ["/crash-risk-index", "/crash-risk-index.md"],
      ["/developers/auth", "/developers/auth.md"],
    ]) {
      await page.goto(pathname);
      const alternate = page.locator(
        'link[rel="alternate"][type="text/markdown"]',
      );
      await expect(alternate).toHaveAttribute("href", /\.md$/);
      expect(new URL((await alternate.getAttribute("href"))!, page.url()).pathname).toBe(
        markdownPath,
      );
    }
  });

  test("renders linked developer resources and a copyable MCP first call", async ({
    page,
  }) => {
    await page.goto("/developers");
    for (const pathname of [
      "/llms.txt",
      "/openapi.json",
      "/sitemap.xml",
      "/agent-instructions",
    ]) {
      await expect(page.locator(`a[href$="${pathname}"]`).first()).toBeVisible();
    }

    await page.goto("/developers/mcp");
    const example = page.locator('pre code[data-language="bash"]');
    await expect(example).toContainText("curl -s -X POST https://app.radon.run/mcp");
    await expect(example).toContainText('"name":"radon_identity"');
    await expect(
      page.getByText("Protected demo and operator calls are not yet agent-completable", {
        exact: false,
      }),
    ).toBeVisible();
  });

  test("serves the advertised Markdown representation with accurate metadata", async ({
    request,
  }) => {
    const response = await request.get("/index.md", {
      headers: { Accept: "text/html" },
    });

    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toBe(
      "text/markdown; charset=utf-8",
    );
    expect(response.headers().vary).toMatch(/(?:^|,\s*)Accept(?:,|$)/i);
    expect(await response.text()).toContain("# Radon Terminal");
  });

  test("returns truthful HTML, Markdown, missing, and unacceptable statuses", async ({
    request,
  }) => {
    const html = await request.get("/", {
      headers: { Accept: "text/html" },
    });
    expect(html.status()).toBe(200);
    expect(html.headers()["content-type"]).toContain("text/html");

    const markdown = await request.get("/", {
      headers: { Accept: "text/markdown" },
    });
    expect(markdown.status()).toBe(200);
    expect(markdown.headers()["content-type"]).toBe(
      "text/markdown; charset=utf-8",
    );

    const missingHtml = await request.get("/agent-readability-missing", {
      headers: { Accept: "text/html" },
    });
    expect(missingHtml.status()).toBe(404);

    const missingMarkdown = await request.get(
      "/agent-readability-missing.md",
    );
    expect(missingMarkdown.status()).toBe(404);
    expect(missingMarkdown.headers()["content-type"]).toBe(
      "text/markdown; charset=utf-8",
    );

    const unacceptable = await request.get("/", {
      headers: { Accept: "application/pdf" },
    });
    expect(unacceptable.status()).toBe(406);
    expect(unacceptable.headers()["content-type"]).toBe(
      "text/plain; charset=utf-8",
    );
  });

  test("keeps the answer visible in initial HTML without JavaScript", async ({
    browser,
  }) => {
    const context = await browser.newContext({ javaScriptEnabled: false });
    const page = await context.newPage();

    try {
      await page.goto("/");
      await expect(
        page.getByRole("heading", {
          level: 1,
          name: "Institutions finish buying before the chart moves.",
        }),
      ).toBeVisible();
      await expect(
        page.getByText(
          "Off-exchange size builds while the lit tape is quiet.",
          { exact: false },
        ),
      ).toBeVisible();
    } finally {
      await context.close();
    }
  });
});
