import * as cheerio from 'cheerio';
import type { SEOIssue, IssueCategory, IssueSeverity } from '../../../shared/types/seo-issues';

/**
 * Accessibility Analyzer
 *
 * Validates:
 * - Image alt text coverage
 * - Alt text quality (not just filename)
 * - Decorative images (empty alt)
 * - Missing ARIA labels for critical elements
 */
export class AccessibilityAnalyzer {
  analyze(html: string, url: string): SEOIssue[] {
    const $ = cheerio.load(html);
    const issues: SEOIssue[] = [];
    const timestamp = new Date().toISOString();

    // Analyze images
    const images = $('img');
    const totalImages = images.length;
    let imagesWithAlt = 0;
    let imagesWithEmptyAlt = 0;
    let imagesWithPoorAlt = 0;

    images.each((_, el) => {
      const $img = $(el);
      const alt = $img.attr('alt');
      const src = $img.attr('src') || '';

      if (alt === undefined) {
        // Missing alt attribute entirely
        issues.push({
          issueId: `missing-alt-${Date.now()}-${Math.random()}`,
          url,
          category: 'accessibility' as IssueCategory,
          severity: 'high' as IssueSeverity,
          title: 'Image Missing Alt Attribute',
          description: 'Image has no alt attribute. This is bad for accessibility and SEO.',
          currentValue: src.substring(0, 100),
          expectedValue: 'Descriptive alt text',
          suggestion: 'Add alt attribute describing the image content or purpose.',
          detectedAt: timestamp,
        });
      } else if (alt === '') {
        // Empty alt (acceptable for decorative images)
        imagesWithEmptyAlt++;
      } else {
        imagesWithAlt++;

        // Check for poor quality alt text
        const poorAltPatterns = [
          /^image$/i,
          /^img\d+$/i,
          /^photo$/i,
          /^picture$/i,
          /\.(jpg|jpeg|png|gif|webp)$/i, // Filename as alt
        ];

        if (poorAltPatterns.some((pattern) => pattern.test(alt))) {
          imagesWithPoorAlt++;
          issues.push({
            issueId: `poor-alt-${Date.now()}-${Math.random()}`,
            url,
            category: 'accessibility' as IssueCategory,
            severity: 'medium' as IssueSeverity,
            title: 'Poor Quality Alt Text',
            description: 'Image alt text is not descriptive (e.g., "image", filename).',
            currentValue: alt,
            expectedValue: 'Descriptive text explaining the image',
            suggestion: 'Replace generic alt text with meaningful description.',
            detectedAt: timestamp,
          });
        }

        // Check if alt text is too long
        if (alt.length > 125) {
          issues.push({
            issueId: `alt-too-long-${Date.now()}-${Math.random()}`,
            url,
            category: 'accessibility' as IssueCategory,
            severity: 'low' as IssueSeverity,
            title: 'Alt Text Too Long',
            description: `Alt text is ${alt.length} characters. Recommend keeping under 125 characters.`,
            currentValue: alt,
            expectedValue: '< 125 characters',
            suggestion: 'Shorten alt text to focus on key image details.',
            detectedAt: timestamp,
          });
        }
      }
    });

    // Overall alt text coverage
    if (totalImages > 0) {
      const coverage = ((imagesWithAlt + imagesWithEmptyAlt) / totalImages) * 100;

      if (coverage < 100) {
        issues.push({
          issueId: `low-alt-coverage-${Date.now()}`,
          url,
          category: 'accessibility' as IssueCategory,
          severity: 'high' as IssueSeverity,
          title: 'Low Alt Text Coverage',
          description: `Only ${coverage.toFixed(0)}% of images have alt attributes (${imagesWithAlt + imagesWithEmptyAlt}/${totalImages}).`,
          currentValue: `${coverage.toFixed(0)}% coverage`,
          expectedValue: '100% coverage',
          suggestion: 'Add alt attributes to all images. Use empty alt="" for decorative images.',
          detectedAt: timestamp,
        });
      }
    }

    // Check for links without accessible text
    $('a').each((_, el) => {
      const $link = $(el);
      const text = $link.text().trim();
      const ariaLabel = $link.attr('aria-label');
      const title = $link.attr('title');

      if (!text && !ariaLabel && !title) {
        const href = $link.attr('href') || '';
        issues.push({
          issueId: `empty-link-${Date.now()}-${Math.random()}`,
          url,
          category: 'accessibility' as IssueCategory,
          severity: 'medium' as IssueSeverity,
          title: 'Link Without Accessible Text',
          description: 'Link has no text, aria-label, or title. Screen readers cannot describe it.',
          currentValue: href.substring(0, 100),
          expectedValue: 'Visible text or aria-label',
          suggestion: 'Add descriptive text or aria-label to the link.',
          detectedAt: timestamp,
        });
      }
    });

    // Check for missing language attribute
    const htmlLang = $('html').attr('lang');
    if (!htmlLang) {
      issues.push({
        issueId: `missing-lang-${Date.now()}`,
        url,
        category: 'accessibility' as IssueCategory,
        severity: 'medium' as IssueSeverity,
        title: 'Missing Language Attribute',
        description: 'HTML tag missing lang attribute. This affects screen readers and SEO.',
        currentValue: undefined,
        expectedValue: '<html lang="en">',
        suggestion: 'Add lang attribute to <html> tag (e.g., lang="en").',
        detectedAt: timestamp,
      });
    }

    return issues;
  }
}
