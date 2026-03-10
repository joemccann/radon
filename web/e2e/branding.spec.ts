import { test, expect } from '@playwright/test';

test.describe('Branding Assets Verification', () => {
  test('should have the correct favicon tags', async ({ page }) => {
    await page.goto('/');
    
    // Check for favicons
    const icon16 = await page.locator('link[rel="icon"][sizes="16x16"]');
    await expect(icon16).toHaveAttribute('href', '/icons/favicon-16x16.png');
    
    const icon32 = await page.locator('link[rel="icon"][sizes="32x32"]');
    await expect(icon32).toHaveAttribute('href', '/icons/favicon-32x32.png');
    
    const icon64 = await page.locator('link[rel="icon"][sizes="64x64"]');
    await expect(icon64).toHaveAttribute('href', '/icons/favicon-64x64.png');
    
    // Check for apple-touch-icon
    const appleIcon = await page.locator('link[rel="apple-touch-icon"]');
    await expect(appleIcon).toHaveAttribute('href', '/icons/apple-touch-icon.png');
  });

  test('should have the correct OpenGraph meta tags', async ({ page }) => {
    await page.goto('/');
    
    // Check OG Title and Description
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', 'Radon Terminal');
    await expect(page.locator('meta[property="og:description"]')).toHaveAttribute('content', 'Reconstructing market structure from noisy signals.');
    
    // Check OG Images
    const ogImages = await page.locator('meta[property="og:image"]');
    await expect(ogImages.nth(0)).toHaveAttribute('content', /.*\/images\/hero-og\.png/);
    await expect(ogImages.nth(1)).toHaveAttribute('content', /.*\/images\/markov-og\.png/);
  });

  test('assets should be reachable', async ({ request }) => {
    const assets = [
      '/icons/favicon-16x16.png',
      '/icons/favicon-32x32.png',
      '/icons/favicon-64x64.png',
      '/icons/apple-touch-icon.png',
      '/images/hero-og.png',
      '/images/markov-og.png'
    ];

    for (const asset of assets) {
      const response = await request.get(asset);
      expect(response.status()).toBe(200);
      expect(response.headers()['content-type']).toContain('image/png');
    }
  });
});
