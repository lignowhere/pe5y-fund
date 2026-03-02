# Crawler Implementation Guide

## Free Tier Strategy (Current)

### Hybrid Crawler Architecture

The SEO automation tool uses a **smart 3-tier crawling strategy** that starts 100% free and scales as needed:

```
┌─────────────────────────────────────────────┐
│  Tier 1: Simple HTTP Fetch (Primary)       │
│  - 100% Free, Unlimited                     │
│  - Fast (<100ms)                            │
│  - Works for 80% of sites                   │
└─────────────────────────────────────────────┘
                    ↓ (if JS needed)
┌─────────────────────────────────────────────┐
│  Tier 2: Cloudflare Browser (Fallback)     │
│  - Free until August 2025                   │
│  - JavaScript execution                     │
│  - Console logs & errors                    │
└─────────────────────────────────────────────┘
                    ↓ (for production scale)
┌─────────────────────────────────────────────┐
│  Tier 3: ScrapingBee API (Paid Upgrade)    │
│  - 1,000 requests/month FREE                │
│  - Then $49/month for 150K requests         │
│  - Full stealth + CAPTCHA solving           │
└─────────────────────────────────────────────┘
```

## Usage

### Test Crawler (Manual Trigger)

```bash
# Test with Simple Fetch (default)
curl -X POST http://localhost:8787/api/trigger-scan \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# Force browser rendering
curl -X POST http://localhost:8787/api/trigger-scan \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "strategy": "browser"}'

# Force simple fetch only
curl -X POST http://localhost:8787/api/trigger-scan \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "strategy": "fetch"}'
```

### Get Crawler Stats

```bash
curl http://localhost:8787/api/crawler/stats
```

**Response:**
```json
{
  "tier": "free",
  "provider": "Simple Fetch + CF Browser",
  "limit": "Unlimited",
  "cost": "$0/month",
  "upgradeOption": "ScrapingBee ($49/month, 150K requests)"
}
```

## How Auto-Selection Works

The hybrid crawler intelligently chooses the best method:

1. **First attempt:** Simple HTTP Fetch
2. **Detection:** Checks HTML for JS framework indicators:
   - `<div id="root"></div>` (React)
   - `ng-app` (Angular)
   - `__NUXT__` (Nuxt.js)
   - Empty body with scripts
3. **Fallback:** Switches to Cloudflare Browser if JS detected
4. **Retry:** If simple fetch fails, tries browser rendering

## Cost Comparison

| Method | Monthly Cost | Requests | Speed | JS Support |
|--------|-------------|----------|-------|------------|
| **Simple Fetch** | $0 | Unlimited | <100ms | ❌ |
| **CF Browser** | $0* | Limited** | 2-5s | ✅ |
| **ScrapingBee** | $0-49 | 1K-150K | 1-3s | ✅ |

*Free until August 2025
**10 concurrent browsers, 2/minute rate limit

## Upgrade to Paid Tier

When you need more scale or advanced features:

### ScrapingBee Setup

1. Sign up at https://www.scrapingbee.com (no phone required)
2. Get API key from dashboard
3. Add secret to Cloudflare:
   ```bash
   wrangler secret put SCRAPINGBEE_API_KEY
   ```

4. Enable in code:
   ```typescript
   // worker/src/clients/scrapingbee-crawler.ts
   export class ScrapingBeeCrawler {
     async crawl(url: string, apiKey: string): Promise<PageData> {
       const response = await fetch(
         `https://app.scrapingbee.com/api/v1/?api_key=${apiKey}&url=${encodeURIComponent(url)}&render_js=true`
       );
       // ... implementation
     }
   }
   ```

### Alternative: ScraperAPI

- Website: https://www.scraperapi.com
- Free tier: 1,000 requests/month
- Paid: $49/month for 100K requests
- Similar setup process

## Testing Different Sites

### Static Sites (Simple Fetch Works)
```bash
# Wikipedia, blogs, basic HTML sites
curl -X POST http://localhost:8787/api/trigger-scan \
  -d '{"url": "https://en.wikipedia.org/wiki/SEO"}'
```

### JavaScript-Heavy Sites (Auto-Falls Back to Browser)
```bash
# React/Vue/Angular apps
curl -X POST http://localhost:8787/api/trigger-scan \
  -d '{"url": "https://react.dev"}'
```

## Performance Metrics

Monitor crawler performance:

```sql
-- Check crawl times in D1
SELECT
  url,
  load_time_ms,
  status_code,
  LENGTH(html) as page_size_bytes,
  json_array_length(errors) as error_count
FROM raw_crawl_data
ORDER BY timestamp DESC
LIMIT 10;
```

## Troubleshooting

### Simple Fetch Fails
**Symptom:** Empty HTML or timeout
**Solution:** Force browser rendering with `strategy: "browser"`

### Browser Rendering Hits Limit
**Symptom:** "10 concurrent browsers exceeded"
**Solution:**
1. Wait 1 minute (2 new browsers/minute limit)
2. Upgrade to ScrapingBee for unlimited scale

### All Methods Fail
**Symptom:** Site blocks all requests
**Solution:** Upgrade to ScrapingBee (has residential proxies + stealth)