import puppeteer from '@cloudflare/puppeteer';
import type { PageData, Bindings } from '../../../shared/types';

/**
 * Cloudflare Browser Rendering Crawler - Free Tier Available
 *
 * Pros:
 * - Native Cloudflare integration
 * - Executes JavaScript
 * - Captures console logs & errors
 * - Free until August 2025
 *
 * Cons:
 * - 10 concurrent browser limit
 * - 2 new browsers/minute rate limit
 * - Slower than simple fetch (~2-5s)
 *
 * Use when: Simple fetch fails or JS rendering required
 */
export class CloudflareBrowserCrawler {
  async fetchPageData(url: string, env: Bindings): Promise<PageData> {
    // Check if browser rendering is available
    if (!env.MYBROWSER) {
      throw new Error('Browser rendering not available in local mode. Use `wrangler dev --remote` or deploy to Cloudflare.');
    }

    const startTime = Date.now();
    let browser;

    try {
      browser = await puppeteer.launch(env.MYBROWSER);
      const page = await browser.newPage();

      // Capture console logs and errors
      const consoleLogs: string[] = [];
      const errors: string[] = [];

      page.on('console', (msg) => {
        consoleLogs.push(`[${msg.type()}] ${msg.text()}`);
      });

      page.on('pageerror', (err) => {
        errors.push(err.message);
      });

      // Set realistic user agent
      await page.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      );

      // Navigate to page
      const response = await page.goto(url, {
        waitUntil: 'networkidle0',
        timeout: 30000,
      });

      // Extract data
      const html = await page.content();
      const headers = response?.headers() || {};
      const statusCode = response?.status() || 0;
      const loadTime = Date.now() - startTime;

      console.log(`✅ Browser rendered ${url} in ${loadTime}ms`);

      return {
        html,
        headers,
        console: consoleLogs,
        errors,
        loadTime,
        statusCode,
      };
    } catch (error) {
      console.error(`❌ Browser rendering failed for ${url}:`, error);
      throw error;
    } finally {
      if (browser) {
        await browser.close();
      }
    }
  }
}