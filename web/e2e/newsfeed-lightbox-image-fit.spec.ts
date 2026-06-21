import { test, expect } from "@playwright/test";

// A deliberately tall portrait image — the case that previously overflowed the
// lightbox panel and got clipped top/bottom (real research charts are often
// portrait, unlike the declared 16:9). 600x1500.
const TALL_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="1500">' +
  '<rect width="600" height="1500" fill="#0b0e13"/>' +
  '<text x="300" y="60" fill="#7fffd4" font-size="40" text-anchor="middle">TOP EDGE</text>' +
  '<text x="300" y="1470" fill="#7fffd4" font-size="40" text-anchor="middle">BOTTOM EDGE</text>' +
  "</svg>";

const POST = {
  id: "lightbox-fit-1",
  title: "VERIFY Tall Chart Clip",
  content: "Portrait research chart that previously clipped top/bottom.",
  timestamp: new Date().toISOString(),
  images: ["/verify-tall.svg"],
  raw_images: ["/verify-tall.svg"],
  tags: ["TEST"],
};

test.describe("newsfeed lightbox image fit", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/newsfeed/posts**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([POST]) }),
    );
    // Next/Image proxies through /_next/image; return the tall SVG for both the
    // optimizer URL and any direct request.
    const tall = { status: 200, contentType: "image/svg+xml", body: TALL_SVG };
    await page.route("**/_next/image**", (route) => route.fulfill(tall));
    await page.route("**/verify-tall.svg", (route) => route.fulfill(tall));
  });

  test("portrait image is fully contained, not clipped, capped to the viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/dashboard");

    await page.getByRole("button", { name: `Open lightbox for: ${POST.title}` }).click();

    const img = page.locator(".newsfeed-lightbox__image");
    await expect(img).toBeVisible();
    await expect(img).toHaveJSProperty("complete", true);

    const m = await img.evaluate((el) => {
      const image = el as HTMLImageElement;
      const media = el.closest(".newsfeed-lightbox__media") as HTMLElement;
      const r = image.getBoundingClientRect();
      const md = media.getBoundingClientRect();
      return {
        natW: image.naturalWidth,
        natH: image.naturalHeight,
        imgTop: r.top,
        imgBottom: r.bottom,
        imgHeight: r.height,
        mediaTop: md.top,
        mediaBottom: md.bottom,
        vh: window.innerHeight,
      };
    });

    // The source really is tall (the regression case).
    expect(m.natH).toBeGreaterThan(m.natW);
    // Fully within the media box — no top/bottom clipping (1px tolerance).
    expect(m.imgTop).toBeGreaterThanOrEqual(m.mediaTop - 1);
    expect(m.imgBottom).toBeLessThanOrEqual(m.mediaBottom + 1);
    // Height capped (the CSS max-height: min(82vh, 780px)) — not the full 1500px.
    expect(m.imgHeight).toBeLessThanOrEqual(Math.round(m.vh * 0.82) + 2);
    expect(m.imgHeight).toBeLessThan(m.natH);
    // Within the viewport.
    expect(m.imgBottom).toBeLessThanOrEqual(m.vh + 1);
  });
});
