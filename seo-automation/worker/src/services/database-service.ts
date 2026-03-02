import type { Bindings, PageData } from '../../../shared/types';
import type { SEOAnalysisResult } from '../../../shared/types/seo-issues';
import type { AIRewriteEngineResult } from './ai-rewrite-engine';

/**
 * Database Service
 *
 * Handles all D1 database operations for crawl data and SEO analysis results.
 */
export class DatabaseService {
  constructor(private db: D1Database) {}

  /**
   * Store raw crawl data in D1
   */
  async storeCrawlData(url: string, data: PageData): Promise<void> {
    try {
      await this.db.prepare(
        `INSERT INTO raw_crawl_data (url, timestamp, html, headers, console_logs, errors, status_code, load_time_ms)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      )
        .bind(
          url,
          new Date().toISOString(),
          data.html,
          JSON.stringify(data.headers),
          JSON.stringify(data.console),
          JSON.stringify(data.errors),
          data.statusCode || 200,
          data.loadTime || 0
        )
        .run();

      console.log(`💾 Stored crawl data for ${url} in D1`);
    } catch (error) {
      console.error('Failed to store crawl data:', error);
      throw error;
    }
  }

  /**
   * Store SEO analysis results in scan_state and issues_current tables
   */
  async storeAnalysisResults(result: SEOAnalysisResult): Promise<void> {
    try {
      // Store scan state with full JSON report
      await this.db.prepare(
        `INSERT INTO scan_state (url, timestamp, json_report, diff_from_previous)
         VALUES (?, ?, ?, ?)`
      )
        .bind(
          result.url,
          result.timestamp,
          JSON.stringify(result),
          null // TODO: Phase 05 - Calculate delta from previous scan
        )
        .run();

      // Clear existing issues for this URL
      await this.db.prepare(`DELETE FROM issues_current WHERE url = ?`)
        .bind(result.url)
        .run();

      // Insert current issues
      for (const issue of result.issues) {
        await this.db.prepare(
          `INSERT INTO issues_current (
            issue_id, url, category, severity, title, description,
            current_value, expected_value, suggestion, ai_fix, priority_score, detected_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
        )
          .bind(
            issue.issueId,
            issue.url,
            issue.category,
            issue.severity,
            issue.title,
            issue.description,
            issue.currentValue || null,
            issue.expectedValue || null,
            issue.suggestion,
            issue.ai_fix || null,
            0, // TODO: Phase 05 - Calculate priority score
            issue.detectedAt
          )
          .run();
      }

      console.log(`💾 Stored ${result.issues.length} SEO issues for ${result.url}`);

      // Cleanup: Keep only last 2 scans per URL
      await this.db.prepare(
        `DELETE FROM scan_state WHERE id NOT IN (
          SELECT id FROM scan_state WHERE url = ? ORDER BY timestamp DESC LIMIT 2
        ) AND url = ?`
      )
        .bind(result.url, result.url)
        .run();
    } catch (error) {
      console.error('Failed to store analysis results:', error);
      throw error;
    }
  }

  /**
   * Get latest scan for a URL
   */
  async getLatestScan(url: string): Promise<SEOAnalysisResult | null> {
    try {
      const result = await this.db.prepare(
        `SELECT json_report FROM scan_state WHERE url = ? ORDER BY timestamp DESC LIMIT 1`
      )
        .bind(url)
        .first();

      if (!result || !result.json_report) {
        return null;
      }

      return JSON.parse(result.json_report as string) as SEOAnalysisResult;
    } catch (error) {
      console.error('Failed to get latest scan:', error);
      return null;
    }
  }

  /**
   * Store AI metadata and usage
   */
  async storeAIMetadata(result: AIRewriteEngineResult): Promise<void> {
    try {
      await this.db.prepare(
        `INSERT INTO ai_metadata (run_timestamp, total_tokens, cached_tokens, cost_usd, pages_processed)
         VALUES (?, ?, ?, ?, ?)`
      )
        .bind(
          new Date().toISOString(),
          result.usage.inputTokens + result.usage.outputTokens,
          result.usage.cacheReadTokens,
          result.cost,
          1
        )
        .run();

      console.log(`💾 Stored AI metadata: $${result.cost.toFixed(4)} for ${result.url}`);
    } catch (error) {
      console.error('Failed to store AI metadata:', error);
      throw error;
    }
  }

  /**
   * Update issues with AI-generated fixes
   */
  async updateIssuesWithAIFixes(url: string, aiFixContent: string): Promise<void> {
    try {
      await this.db.prepare(
        `UPDATE issues_current SET ai_fix = ? WHERE url = ? AND category IN ('meta', 'header', 'accessibility')`
      )
        .bind(aiFixContent, url)
        .run();

      console.log(`✅ Updated issues with AI fixes for ${url}`);
    } catch (error) {
      console.error('Failed to update issues with AI fixes:', error);
      throw error;
    }
  }
}
