import type { PageData, Bindings } from '../../../shared/types';
import { SimpleFetchCrawler } from './simple-fetch-crawler';
import { CloudflareBrowserCrawler } from './cloudflare-browser-crawler';

/**
 * Hybrid Crawler - Smart Tier Selection
 *
 * Strategy:
 * 1. Try Simple Fetch first (free, fast)
 * 2. Fallback to Cloudflare Browser if needed
 * 3. Future: ScrapingBee for production scale
 *
 * Upgrade Path:
 * Free Tier → Simple Fetch + CF Browser (free until Aug 2025)
 * Paid Tier → ScrapingBee API ($49/month, 150K requests)
 */
export class HybridCrawler {
  private simpleFetch: SimpleFetchCrawler;
  private browserCrawler: CloudflareBrowserCrawler;

  constructor() {
    this.simpleFetch = new SimpleFetchCrawler();
    this.browserCrawler = new CloudflareBrowserCrawler();
  }

  async crawl(url: string, env: Bindings, options?: CrawlOptions): Promise<PageData> {
    const strategy = options?.strategy || 'auto';

    try {
      // Force browser rendering if requested
      if (strategy === 'browser') {
        console.log(`🌐 Using browser rendering for ${url}`);
        return await this.browserCrawler.fetchPageData(url, env);
      }

      // Force simple fetch if requested
      if (strategy === 'fetch') {
        console.log(`⚡ Using simple fetch for ${url}`);
        return await this.simpleFetch.fetchPageData(url);
      }

      // Auto: Try simple fetch first, fallback to browser
      console.log(`🔄 Auto-selecting crawler for ${url}`);

      try {
        const data = await this.simpleFetch.fetchPageData(url);

        // Check if JavaScript rendering is likely needed
        if (this.needsJavaScript(data.html)) {
          console.log(`🔄 Detected JS-heavy site, switching to browser rendering`);
          return await this.browserCrawler.fetchPageData(url, env);
        }

        return data;
      } catch (fetchError) {
        console.log(`⚠️ Simple fetch failed, trying browser rendering...`);
        return await this.browserCrawler.fetchPageData(url, env);
      }
    } catch (error) {
      console.error(`❌ All crawl methods failed for ${url}:`, error);
      throw new Error(`Crawl failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }

  /**
   * Detect if page needs JavaScript rendering
   */
  private needsJavaScript(html: string): boolean {
    const indicators = [
      // React/Vue/Angular apps
      /<div[^>]+id=["']root["'][^>]*><\/div>/i,
      /<div[^>]+id=["']app["'][^>]*><\/div>/i,
      /data-reactroot/i,
      /ng-app/i,
      /__NUXT__/i,

      // Empty body with scripts
      /<body[^>]*>\s*<script/i,

      // Common SPA frameworks
      /react\.min\.js/i,
      /vue\.min\.js/i,
      /angular\.min\.js/i,
    ];

    return indicators.some((pattern) => pattern.test(html));
  }

  /**
   * Get crawler statistics
   */
  getStats(): CrawlerStats {
    return {
      tier: 'free',
      provider: 'Simple Fetch + CF Browser',
      limit: 'Unlimited',
      cost: '$0/month',
      upgradeOption: 'ScrapingBee ($49/month, 150K requests)',
    };
  }
}

export interface CrawlOptions {
  strategy?: 'auto' | 'fetch' | 'browser';
  timeout?: number;
}

export interface CrawlerStats {
  tier: 'free' | 'paid';
  provider: string;
  limit: string;
  cost: string;
  upgradeOption?: string;
}