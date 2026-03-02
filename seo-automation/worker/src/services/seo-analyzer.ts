import * as cheerio from 'cheerio';
import type { SEOIssue, SEOAnalysisResult } from '../../../shared/types/seo-issues';
import { MetaTagAnalyzer } from '../analyzers/meta-tag-analyzer';
import { HeaderAnalyzer } from '../analyzers/header-analyzer';
import { AccessibilityAnalyzer } from '../analyzers/accessibility-analyzer';

/**
 * SEO Analyzer Service
 *
 * Orchestrates all SEO analyzers and produces comprehensive analysis results.
 */
export class SEOAnalyzer {
  private metaAnalyzer: MetaTagAnalyzer;
  private headerAnalyzer: HeaderAnalyzer;
  private accessibilityAnalyzer: AccessibilityAnalyzer;

  constructor() {
    this.metaAnalyzer = new MetaTagAnalyzer();
    this.headerAnalyzer = new HeaderAnalyzer();
    this.accessibilityAnalyzer = new AccessibilityAnalyzer();
  }

  /**
   * Analyze HTML content for SEO issues
   */
  async analyze(html: string, url: string): Promise<SEOAnalysisResult> {
    const startTime = Date.now();
    const $ = cheerio.load(html);

    // Run all analyzers in parallel
    const [metaIssues, headerIssues, accessibilityIssues] = await Promise.all([
      Promise.resolve(this.metaAnalyzer.analyze(html, url)),
      Promise.resolve(this.headerAnalyzer.analyze(html, url)),
      Promise.resolve(this.accessibilityAnalyzer.analyze(html, url)),
    ]);

    // Combine all issues
    const allIssues: SEOIssue[] = [
      ...metaIssues,
      ...headerIssues,
      ...accessibilityIssues,
    ];

    // Calculate summary statistics
    const summary = this.calculateSummary(allIssues);

    // Extract metadata
    const metadata = this.extractMetadata($);

    const analysisTime = Date.now() - startTime;
    console.log(`📊 SEO Analysis completed in ${analysisTime}ms - Found ${allIssues.length} issues`);

    return {
      url,
      timestamp: new Date().toISOString(),
      issues: allIssues,
      summary,
      metadata,
    };
  }

  /**
   * Calculate summary statistics from issues
   */
  private calculateSummary(issues: SEOIssue[]) {
    return {
      totalIssues: issues.length,
      critical: issues.filter((i) => i.severity === 'critical').length,
      high: issues.filter((i) => i.severity === 'high').length,
      medium: issues.filter((i) => i.severity === 'medium').length,
      low: issues.filter((i) => i.severity === 'low').length,
    };
  }

  /**
   * Extract page metadata for reporting
   */
  private extractMetadata($: cheerio.CheerioAPI) {
    const title = $('title').text().trim();
    const description = $('meta[name="description"]').attr('content')?.trim();
    const h1 = $('h1').first().text().trim();

    const images = $('img');
    const totalImages = images.length;
    const imagesWithAlt = images.filter((_, el) => {
      const alt = $(el).attr('alt');
      return alt !== undefined && alt !== '';
    }).length;

    return {
      title: title || undefined,
      description: description || undefined,
      h1: h1 || undefined,
      totalImages,
      imagesWithAlt,
    };
  }

  /**
   * Get top priority issues (critical and high severity)
   */
  getTopIssues(result: SEOAnalysisResult, limit: number = 5): SEOIssue[] {
    return result.issues
      .filter((issue) => issue.severity === 'critical' || issue.severity === 'high')
      .slice(0, limit);
  }

  /**
   * Group issues by category
   */
  groupByCategory(result: SEOAnalysisResult): Record<string, SEOIssue[]> {
    const grouped: Record<string, SEOIssue[]> = {};

    result.issues.forEach((issue) => {
      if (!grouped[issue.category]) {
        grouped[issue.category] = [];
      }
      grouped[issue.category].push(issue);
    });

    return grouped;
  }
}
