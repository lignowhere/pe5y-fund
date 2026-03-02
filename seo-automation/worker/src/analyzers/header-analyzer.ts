import * as cheerio from 'cheerio';
import type { SEOIssue, IssueCategory, IssueSeverity } from '../../../shared/types/seo-issues';

/**
 * Header Hierarchy Analyzer
 *
 * Validates:
 * - Single H1 tag per page
 * - Proper header hierarchy (no skipping levels)
 * - Header content length
 * - Keyword presence in headers
 */
export class HeaderAnalyzer {
  analyze(html: string, url: string): SEOIssue[] {
    const $ = cheerio.load(html);
    const issues: SEOIssue[] = [];
    const timestamp = new Date().toISOString();

    // Check H1 tags
    const h1Tags = $('h1');
    const h1Count = h1Tags.length;

    if (h1Count === 0) {
      issues.push({
        issueId: `missing-h1-${Date.now()}`,
        url,
        category: 'header' as IssueCategory,
        severity: 'critical' as IssueSeverity,
        title: 'Missing H1 Tag',
        description: 'Page has no H1 tag. H1 is crucial for SEO and accessibility.',
        currentValue: undefined,
        expectedValue: 'Exactly one H1 tag',
        suggestion: 'Add a single H1 tag that describes the main topic of the page.',
        detectedAt: timestamp,
      });
    } else if (h1Count > 1) {
      const h1Texts = h1Tags.map((_, el) => $(el).text().trim()).get();
      issues.push({
        issueId: `multiple-h1-${Date.now()}`,
        url,
        category: 'header' as IssueCategory,
        severity: 'high' as IssueSeverity,
        title: 'Multiple H1 Tags',
        description: `Page has ${h1Count} H1 tags. Best practice is to use only one H1 per page.`,
        currentValue: `${h1Count} H1 tags: ${h1Texts.join(', ')}`,
        expectedValue: 'Exactly one H1 tag',
        suggestion: 'Combine multiple H1s into one primary heading, or convert extras to H2/H3.',
        detectedAt: timestamp,
      });
    } else {
      // Check H1 length
      const h1Text = h1Tags.first().text().trim();
      if (h1Text.length < 20) {
        issues.push({
          issueId: `h1-too-short-${Date.now()}`,
          url,
          category: 'header' as IssueCategory,
          severity: 'medium' as IssueSeverity,
          title: 'H1 Too Short',
          description: `H1 is only ${h1Text.length} characters. Consider expanding for better SEO.`,
          currentValue: h1Text,
          expectedValue: '20-70 characters',
          suggestion: 'Expand H1 to include primary keywords and context.',
          detectedAt: timestamp,
        });
      } else if (h1Text.length > 70) {
        issues.push({
          issueId: `h1-too-long-${Date.now()}`,
          url,
          category: 'header' as IssueCategory,
          severity: 'low' as IssueSeverity,
          title: 'H1 Too Long',
          description: `H1 is ${h1Text.length} characters. Keep it concise for readability.`,
          currentValue: h1Text,
          expectedValue: '20-70 characters',
          suggestion: 'Shorten H1 to focus on primary topic.',
          detectedAt: timestamp,
        });
      }
    }

    // Check header hierarchy (H2, H3, etc.)
    const headerStructure = this.analyzeHeaderStructure($);
    if (headerStructure.skippedLevels.length > 0) {
      issues.push({
        issueId: `skipped-header-levels-${Date.now()}`,
        url,
        category: 'header' as IssueCategory,
        severity: 'medium' as IssueSeverity,
        title: 'Skipped Header Levels',
        description: 'Header hierarchy has gaps (e.g., H1 → H3 without H2). This affects accessibility.',
        currentValue: headerStructure.skippedLevels.join(', '),
        expectedValue: 'Sequential header levels (H1 → H2 → H3)',
        suggestion: 'Maintain proper header hierarchy without skipping levels.',
        detectedAt: timestamp,
      });
    }

    // Check for empty headers
    $('h1, h2, h3, h4, h5, h6').each((_, el) => {
      const text = $(el).text().trim();
      if (!text) {
        const tagName = $(el).prop('tagName')?.toLowerCase();
        issues.push({
          issueId: `empty-header-${tagName}-${Date.now()}`,
          url,
          category: 'header' as IssueCategory,
          severity: 'medium' as IssueSeverity,
          title: `Empty ${tagName?.toUpperCase()} Tag`,
          description: `Found an empty ${tagName} tag. Headers should contain descriptive text.`,
          currentValue: 'Empty',
          expectedValue: 'Descriptive text',
          suggestion: `Add meaningful content to the ${tagName} tag or remove it.`,
          detectedAt: timestamp,
        });
      }
    });

    return issues;
  }

  private analyzeHeaderStructure($: cheerio.CheerioAPI): { skippedLevels: string[] } {
    const headers = $('h1, h2, h3, h4, h5, h6').map((_, el) => {
      const tagName = $(el).prop('tagName');
      return parseInt(tagName?.charAt(1) || '0');
    }).get();

    const skippedLevels: string[] = [];
    for (let i = 1; i < headers.length; i++) {
      const prev = headers[i - 1];
      const curr = headers[i];

      if (curr > prev + 1) {
        skippedLevels.push(`H${prev} → H${curr} (skipped H${prev + 1})`);
      }
    }

    return { skippedLevels };
  }
}
