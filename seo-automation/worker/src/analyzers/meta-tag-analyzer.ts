import * as cheerio from 'cheerio';
import type { SEOIssue, IssueCategory, IssueSeverity } from '../../../shared/types/seo-issues';

/**
 * Meta Tag Analyzer
 *
 * Validates:
 * - Title tag (50-60 chars optimal)
 * - Meta description (150-160 chars optimal)
 * - Open Graph tags (og:title, og:description, og:image)
 * - Twitter Card tags
 * - Canonical URL
 * - Viewport meta tag
 */
export class MetaTagAnalyzer {
  analyze(html: string, url: string): SEOIssue[] {
    const $ = cheerio.load(html);
    const issues: SEOIssue[] = [];
    const timestamp = new Date().toISOString();

    // Check title tag
    const title = $('title').text().trim();
    if (!title) {
      issues.push(this.createIssue(
        url,
        'missing-title',
        'critical',
        'Missing Title Tag',
        'Page has no <title> tag. This is critical for SEO.',
        undefined,
        '50-60 characters describing the page',
        'Add a descriptive <title> tag in the <head> section.',
        timestamp
      ));
    } else if (title.length < 30) {
      issues.push(this.createIssue(
        url,
        'title-too-short',
        'high',
        'Title Tag Too Short',
        `Title is only ${title.length} characters. Optimal length is 50-60 characters.`,
        title,
        '50-60 characters',
        'Expand title to include primary keywords and page context.',
        timestamp
      ));
    } else if (title.length > 60) {
      issues.push(this.createIssue(
        url,
        'title-too-long',
        'medium',
        'Title Tag Too Long',
        `Title is ${title.length} characters. Google truncates at ~60 characters.`,
        title,
        '50-60 characters',
        'Shorten title to focus on primary keywords.',
        timestamp
      ));
    }

    // Check meta description
    const description = $('meta[name="description"]').attr('content')?.trim();
    if (!description) {
      issues.push(this.createIssue(
        url,
        'missing-description',
        'high',
        'Missing Meta Description',
        'No meta description tag found. This affects click-through rates in search results.',
        undefined,
        '150-160 characters describing the page',
        'Add a compelling <meta name="description" content="..."> tag.',
        timestamp
      ));
    } else if (description.length < 120) {
      issues.push(this.createIssue(
        url,
        'description-too-short',
        'medium',
        'Meta Description Too Short',
        `Description is only ${description.length} characters. Optimal is 150-160 characters.`,
        description,
        '150-160 characters',
        'Expand description to provide more context and keywords.',
        timestamp
      ));
    } else if (description.length > 160) {
      issues.push(this.createIssue(
        url,
        'description-too-long',
        'medium',
        'Meta Description Too Long',
        `Description is ${description.length} characters. Google truncates at ~160 characters.`,
        description,
        '150-160 characters',
        'Shorten description to avoid truncation in search results.',
        timestamp
      ));
    }

    // Check viewport meta tag (critical for mobile)
    const viewport = $('meta[name="viewport"]').attr('content');
    if (!viewport) {
      issues.push(this.createIssue(
        url,
        'missing-viewport',
        'high',
        'Missing Viewport Meta Tag',
        'No viewport meta tag. This causes mobile rendering issues.',
        undefined,
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        'Add viewport meta tag for proper mobile rendering.',
        timestamp
      ));
    }

    // Check canonical URL
    const canonical = $('link[rel="canonical"]').attr('href');
    if (!canonical) {
      issues.push(this.createIssue(
        url,
        'missing-canonical',
        'medium',
        'Missing Canonical URL',
        'No canonical tag found. This can cause duplicate content issues.',
        undefined,
        `<link rel="canonical" href="${url}">`,
        'Add canonical tag to specify the preferred version of this page.',
        timestamp
      ));
    }

    // Check Open Graph tags
    const ogTitle = $('meta[property="og:title"]').attr('content');
    const ogDescription = $('meta[property="og:description"]').attr('content');
    const ogImage = $('meta[property="og:image"]').attr('content');

    if (!ogTitle || !ogDescription || !ogImage) {
      issues.push(this.createIssue(
        url,
        'incomplete-opengraph',
        'low',
        'Incomplete Open Graph Tags',
        'Missing some Open Graph tags. This affects social media sharing appearance.',
        `og:title: ${ogTitle ? '✓' : '✗'}, og:description: ${ogDescription ? '✓' : '✗'}, og:image: ${ogImage ? '✓' : '✗'}`,
        'All three tags present',
        'Add missing Open Graph tags for better social media sharing.',
        timestamp
      ));
    }

    // Check Twitter Card tags
    const twitterCard = $('meta[name="twitter:card"]').attr('content');
    if (!twitterCard) {
      issues.push(this.createIssue(
        url,
        'missing-twitter-card',
        'low',
        'Missing Twitter Card Tag',
        'No Twitter Card tag found. This affects Twitter sharing appearance.',
        undefined,
        '<meta name="twitter:card" content="summary_large_image">',
        'Add Twitter Card tags for better Twitter sharing.',
        timestamp
      ));
    }

    // Check robots meta tag
    const robots = $('meta[name="robots"]').attr('content');
    if (robots && (robots.includes('noindex') || robots.includes('nofollow'))) {
      issues.push(this.createIssue(
        url,
        'restrictive-robots',
        'critical',
        'Restrictive Robots Meta Tag',
        `Page has "${robots}" in robots meta tag. This blocks search engines.`,
        robots,
        'index, follow',
        'Remove or modify robots meta tag to allow search engine indexing.',
        timestamp
      ));
    }

    return issues;
  }

  private createIssue(
    url: string,
    issueId: string,
    severity: IssueSeverity,
    title: string,
    description: string,
    currentValue: string | undefined,
    expectedValue: string,
    suggestion: string,
    detectedAt: string
  ): SEOIssue {
    return {
      issueId: `${issueId}-${Date.now()}`,
      url,
      category: 'meta' as IssueCategory,
      severity,
      title,
      description,
      currentValue,
      expectedValue,
      suggestion,
      detectedAt,
    };
  }
}
