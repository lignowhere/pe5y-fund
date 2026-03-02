import type { PageData } from '../../../shared/types';

/**
 * Simple HTTP Fetch Crawler - 100% Free, No Limits
 *
 * Pros:
 * - Extremely fast (<100ms)
 * - Zero cost, unlimited requests
 * - Works for 80% of websites (static content)
 *
 * Cons:
 * - No JavaScript execution
 * - Can't capture console logs
 * - Misses dynamically loaded content
 */
export class SimpleFetchCrawler {
  async fetchPageData(url: string): Promise<PageData> {
    const startTime = Date.now();

    try {
      const response = await fetch(url, {
        headers: {
          'User-Agent': this.getRandomUserAgent(),
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9',
          'Accept-Encoding': 'gzip, deflate, br',
          'DNT': '1',
          'Connection': 'keep-alive',
          'Upgrade-Insecure-Requests': '1',
        },
        cf: {
          cacheTtl: 300,
          cacheEverything: false,
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const html = await response.text();
      const headers = Object.fromEntries(response.headers.entries());
      const loadTime = Date.now() - startTime;

      console.log(`✅ Fetched ${url} in ${loadTime}ms`);

      return {
        html,
        headers,
        console: [], // Not available with simple fetch
        errors: [],
        loadTime,
        statusCode: response.status,
      };
    } catch (error) {
      console.error(`❌ Fetch failed for ${url}:`, error);
      throw error;
    }
  }

  private getRandomUserAgent(): string {
    const agents = [
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ];
    return agents[Math.floor(Math.random() * agents.length)];
  }
}