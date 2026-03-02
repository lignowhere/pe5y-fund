import type { DurableObjectState } from '@cloudflare/workers-types';
import type { Bindings } from '../../../shared/types';
import { HybridCrawler } from '../clients/hybrid-crawler';
import { SEOAnalyzer } from '../services/seo-analyzer';
import { DatabaseService } from '../services/database-service';
import { AIRewriteEngine } from '../services/ai-rewrite-engine';

export class SiteCrawler {
  private state: DurableObjectState;
  private env: Bindings;
  private crawler: HybridCrawler;
  private seoAnalyzer: SEOAnalyzer;
  private dbService: DatabaseService;

  constructor(state: DurableObjectState, env: Bindings) {
    this.state = state;
    this.env = env;
    this.crawler = new HybridCrawler();
    this.seoAnalyzer = new SEOAnalyzer();
    this.dbService = new DatabaseService(env.DB);
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    switch (url.pathname) {
      case '/trigger-scan':
        return this.triggerScan(request);
      case '/status':
        return this.getStatus();
      case '/stats':
        return this.getStats();
      default:
        return new Response('Not found', { status: 404 });
    }
  }

  private async triggerScan(request: Request): Promise<Response> {
    try {
      const { targetUrl, strategy } = (await request.json()) as {
        targetUrl: string;
        strategy?: 'auto' | 'fetch' | 'browser';
      };

      console.log(`🔍 Starting scan for: ${targetUrl}`);
      const startTime = Date.now();

      // Step 1: Crawl the page
      const pageData = await this.crawler.crawl(targetUrl, this.env, { strategy });

      // Step 2: Analyze SEO
      const analysisResult = await this.seoAnalyzer.analyze(pageData.html, targetUrl);

      // Step 3: Store results in D1
      await this.dbService.storeCrawlData(targetUrl, pageData);
      await this.dbService.storeAnalysisResults(analysisResult);

      // Step 4: Generate AI fixes (if API key available)
      let aiResult;
      if (this.env.ANTHROPIC_API_KEY) {
        try {
          const aiEngine = new AIRewriteEngine(this.env.ANTHROPIC_API_KEY);
          aiResult = await aiEngine.generateFixes(analysisResult, 'seo-heavy');

          // Store AI metadata and update issues
          await this.dbService.storeAIMetadata(aiResult);
          const aiFixContent = aiEngine.formatForStorage(aiResult);
          await this.dbService.updateIssuesWithAIFixes(targetUrl, aiFixContent);
        } catch (aiError) {
          console.error('AI generation failed:', aiError);
          // Continue without AI fixes
        }
      } else {
        console.log('⚠️ ANTHROPIC_API_KEY not set, skipping AI fix generation');
      }

      // Update DO state
      await this.state.storage.put('lastScanTime', new Date().toISOString());
      await this.state.storage.put('lastScanUrl', targetUrl);

      const duration = Date.now() - startTime;
      console.log(`✅ Scan completed for ${targetUrl} in ${duration}ms - Found ${analysisResult.issues.length} issues`);

      return new Response(
        JSON.stringify({
          status: 'success',
          targetUrl,
          duration,
          pageSize: pageData.html.length,
          consoleErrors: pageData.errors.length,
          statusCode: pageData.statusCode,
          seoAnalysis: {
            totalIssues: analysisResult.summary.totalIssues,
            critical: analysisResult.summary.critical,
            high: analysisResult.summary.high,
            medium: analysisResult.summary.medium,
            low: analysisResult.summary.low,
            topIssues: this.seoAnalyzer.getTopIssues(analysisResult, 3),
          },
          aiGenerated: aiResult ? {
            title: aiResult.aiContent.title,
            description: aiResult.aiContent.description,
            cost: aiResult.cost,
            validationErrors: aiResult.validation.errors.length,
          } : null,
        }),
        {
          headers: { 'Content-Type': 'application/json' },
        }
      );
    } catch (error) {
      console.error('Scan error:', error);
      return new Response(
        JSON.stringify({
          status: 'error',
          error: error instanceof Error ? error.message : 'Unknown error',
        }),
        {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        }
      );
    }
  }

  private async getStatus(): Promise<Response> {
    const lastScan = await this.state.storage.get<string>('lastScanTime');
    const lastUrl = await this.state.storage.get<string>('lastScanUrl');

    return new Response(
      JSON.stringify({
        status: 'idle',
        lastScan: lastScan || null,
        lastUrl: lastUrl || null,
      }),
      {
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }

  private async getStats(): Response {
    const stats = this.crawler.getStats();
    return new Response(JSON.stringify(stats), {
      headers: { 'Content-Type': 'application/json' },
    });
  }
}