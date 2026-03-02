# SEO Automation Tool

Cloudflare-optimized daily SEO scanner with AI-powered content optimization.

## Architecture

- **Worker:** Cloudflare Workers with Hono API framework
- **Database:** D1 SQLite (stores only last 2 scans)
- **AI Engine:** Claude 3.5 Sonnet with prompt caching
- **Crawler:** Browserbase (50 pages/day)
- **Dashboard:** React SPA with TanStack Query
- **Scheduling:** Cloudflare Cron Triggers (00:00 UTC daily)

## Project Structure

```
seo-automation/
├── worker/                    # Cloudflare Worker
│   ├── src/
│   │   ├── index.ts          # Main entry point
│   │   ├── api/              # API routes
│   │   ├── clients/          # Browserbase & Claude clients
│   │   ├── db/               # D1 schema
│   │   └── durable-objects/  # Site crawler DO
│   ├── wrangler.toml         # Cloudflare config
│   └── package.json
├── dashboard/                 # React dashboard
│   ├── src/
│   │   ├── App.tsx
│   │   └── pages/
│   └── package.json
└── shared/
    └── types/                 # Shared TypeScript types
```

## Phase 01 Setup (Completed)

### Prerequisites

- Node.js 20+
- Cloudflare account
- Wrangler CLI: `npm install -g wrangler`

### Installation

1. **Install Worker dependencies**
```bash
cd worker
npm install
```

2. **Install Dashboard dependencies**
```bash
cd ../dashboard
npm install
```

### Configuration

1. **Create D1 Database**
```bash
cd worker
wrangler d1 create seo-automation-db
# Copy database_id from output and update wrangler.toml
```

2. **Initialize Database Schema**
```bash
wrangler d1 execute seo-automation-db --local --file=./src/db/schema.sql
```

3. **Set API Keys (Production)**
```bash
wrangler secret put BROWSERBASE_API_KEY
wrangler secret put ANTHROPIC_API_KEY
```

### Development

1. **Start Worker (local)**
```bash
cd worker
wrangler dev
# Worker runs at http://localhost:8787
```

2. **Start Dashboard (local)**
```bash
cd dashboard
npm run dev
# Dashboard runs at http://localhost:3000
```

3. **Test Health Check**
```bash
curl http://localhost:8787/health
```

### Deployment

1. **Deploy Worker**
```bash
cd worker
wrangler deploy
```

2. **Deploy Dashboard to Pages**
```bash
cd dashboard
npm run build
wrangler pages deploy ./dist --project-name=seo-dashboard
```

## API Endpoints

- `GET /` - Worker info
- `GET /health` - System health check
- `GET /api/scans/latest` - Get last 2 scans
- `GET /api/scans/issues` - Get current issues

## Configuration

### AI Tone (Phase 04)
Current: **SEO-heavy** (keyword-dense, optimization-first)

### Delivery Method
- ✅ Dashboard
- ✅ Email summary (Phase 09)

### Scale
50 pages/day (Cloudflare Browser Rendering)

## Implementation Status

| Phase | Status | Description |
|-------|--------|-------------|
| 01 | ✅ Complete | Foundation & Infrastructure |
| 02 | ⏳ Pending | Crawler Agent |
| 03 | ⏳ Pending | SEO Analyzer Engine |
| 04 | ⏳ Pending | AI Rewrite Integration |
| 05 | ⏳ Pending | Priority & Delta Engine |
| 06 | ⏳ Pending | Cloudflare Optimization Detector |
| 07 | ⏳ Pending | Dashboard UI |
| 08 | ⏳ Pending | Testing & Deployment |

## Next Steps

→ [Phase 02: Crawler Agent Implementation](../plans/251206-2110-seo-automation-tool/phase-02-crawler-agent.md)

## Resources

- [Implementation Plan](../plans/251206-2110-seo-automation-tool/plan.md)
- [Cloudflare Workers Docs](https://developers.cloudflare.com/workers/)
- [Hono Framework](https://hono.dev/)
- [Browserbase Docs](https://docs.browserbase.com/)
- [Claude API Docs](https://docs.anthropic.com/)