import { Hono } from 'hono';
import { cors } from 'hono/cors';
import type { Bindings } from '../../shared/types';
import health from './api/health';
import scans from './api/scans';

const app = new Hono<{ Bindings: Bindings }>();

// CORS middleware for dashboard
app.use('/*', cors({
  origin: '*', // TODO: Restrict to dashboard domain in production
  allowMethods: ['GET', 'POST', 'OPTIONS'],
  allowHeaders: ['Content-Type'],
}));

// API routes
app.route('/health', health);
app.route('/api/scans', scans);

// Manual scan trigger (for testing)
app.post('/api/trigger-scan', async (c) => {
  try {
    const { url, strategy } = await c.req.json<{
      url: string;
      strategy?: 'auto' | 'fetch' | 'browser';
    }>();

    if (!url) {
      return c.json({ error: 'URL is required' }, 400);
    }

    // Get Durable Object stub
    const id = c.env.SITE_CRAWLER.idFromName(url);
    const stub = c.env.SITE_CRAWLER.get(id);

    // Trigger scan
    const response = await stub.fetch('https://fake-host/trigger-scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targetUrl: url, strategy }),
    });

    const result = await response.json();
    return c.json(result);
  } catch (error) {
    return c.json(
      { error: error instanceof Error ? error.message : 'Failed to trigger scan' },
      500
    );
  }
});

// Get crawler stats
app.get('/api/crawler/stats', async (c) => {
  try {
    const id = c.env.SITE_CRAWLER.idFromName('default');
    const stub = c.env.SITE_CRAWLER.get(id);
    const response = await stub.fetch('https://fake-host/stats');
    const stats = await response.json();
    return c.json(stats);
  } catch (error) {
    return c.json(
      { error: error instanceof Error ? error.message : 'Failed to get stats' },
      500
    );
  }
});

// Root endpoint
app.get('/', (c) => {
  return c.json({
    name: 'SEO Automation Worker',
    version: '2.0.0',
    phase: 'Phase 02 - Crawler Implementation',
    crawler: 'Hybrid (Simple Fetch + CF Browser)',
    tier: 'Free',
    endpoints: [
      'GET /health',
      'GET /api/scans/latest',
      'GET /api/scans/issues',
      'POST /api/trigger-scan',
      'GET /api/crawler/stats',
    ],
  });
});

export default {
  fetch: app.fetch,

  async scheduled(event: ScheduledEvent, env: Bindings, ctx: ExecutionContext) {
    console.log('🕐 Daily SEO scan triggered at', new Date(event.scheduledTime).toISOString());

    ctx.waitUntil(
      (async () => {
        try {
          // TODO: Get target URL from config table
          // For now, using placeholder
          const targetUrl = 'https://example.com';

          // Get Durable Object for this site
          const id = env.SITE_CRAWLER.idFromName(targetUrl);
          const stub = env.SITE_CRAWLER.get(id);

          // Trigger scan
          const response = await stub.fetch('https://fake-host/trigger-scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              targetUrl,
              strategy: 'auto', // Auto-select best crawler
            }),
          });

          const result = await response.json();
          console.log('📊 Scheduled scan result:', result);

          // TODO Phase 03: Analyze crawl data for SEO issues
          // TODO Phase 04: Generate AI-powered fixes
          // TODO Phase 05: Calculate delta from previous scan
        } catch (error) {
          console.error('❌ Scheduled scan failed:', error);
        }
      })()
    );
  },
};

// Export Durable Objects
export { SiteCrawler } from './durable-objects/site-crawler';