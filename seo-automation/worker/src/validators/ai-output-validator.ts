import type { AIRewriteResult } from '../clients/claude-client';

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/**
 * AI Output Validator
 *
 * Validates Claude-generated SEO content meets length constraints and format requirements.
 */
export class AIOutputValidator {
  /**
   * Validate AI-generated SEO content
   */
  validate(result: AIRewriteResult): ValidationResult {
    const errors: string[] = [];
    const warnings: string[] = [];

    // Validate title
    if (!result.title) {
      errors.push('Title is missing');
    } else if (result.title.length < 30) {
      warnings.push(`Title too short (${result.title.length} chars, recommend 50-60)`);
    } else if (result.title.length > 70) {
      errors.push(`Title too long (${result.title.length} chars, max 70)`);
    }

    // Validate meta description
    if (!result.description) {
      errors.push('Description is missing');
    } else if (result.description.length < 120) {
      warnings.push(`Description too short (${result.description.length} chars, recommend 150-160)`);
    } else if (result.description.length > 170) {
      errors.push(`Description too long (${result.description.length} chars, max 170)`);
    }

    // Validate H1
    if (!result.h1) {
      errors.push('H1 is missing');
    } else if (result.h1.length < 20) {
      warnings.push(`H1 too short (${result.h1.length} chars, recommend 20-70)`);
    } else if (result.h1.length > 80) {
      errors.push(`H1 too long (${result.h1.length} chars, max 80)`);
    }

    // Validate OG title
    if (!result.ogTitle) {
      warnings.push('OG title is missing');
    } else if (result.ogTitle.length > 70) {
      errors.push(`OG title too long (${result.ogTitle.length} chars, max 70)`);
    }

    // Validate OG description
    if (!result.ogDescription) {
      warnings.push('OG description is missing');
    } else if (result.ogDescription.length > 200) {
      errors.push(`OG description too long (${result.ogDescription.length} chars, max 200)`);
    }

    // Validate alt texts
    if (result.altTexts && result.altTexts.length > 0) {
      result.altTexts.forEach((alt, index) => {
        if (alt.length > 125) {
          errors.push(`Alt text ${index + 1} too long (${alt.length} chars, max 125)`);
        }
        if (alt.length < 5) {
          warnings.push(`Alt text ${index + 1} too short (${alt.length} chars, recommend 20-125)`);
        }
      });
    }

    // Validate schema type
    const validSchemaTypes = ['Organization', 'Product', 'WebPage', 'Article', 'LocalBusiness', 'Event', 'Person'];
    if (result.schemaType && !validSchemaTypes.includes(result.schemaType)) {
      warnings.push(`Unusual schema type: ${result.schemaType}`);
    }

    return {
      valid: errors.length === 0,
      errors,
      warnings,
    };
  }

  /**
   * Auto-fix common issues (trim to max length)
   */
  autoFix(result: AIRewriteResult): AIRewriteResult {
    return {
      ...result,
      title: result.title ? this.truncate(result.title, 70) : '',
      description: result.description ? this.truncate(result.description, 170) : '',
      h1: result.h1 ? this.truncate(result.h1, 80) : '',
      ogTitle: result.ogTitle ? this.truncate(result.ogTitle, 70) : '',
      ogDescription: result.ogDescription ? this.truncate(result.ogDescription, 200) : '',
      altTexts: result.altTexts?.map((alt) => this.truncate(alt, 125)) || [],
    };
  }

  /**
   * Truncate text to max length at word boundary
   */
  private truncate(text: string, maxLength: number): string {
    if (text.length <= maxLength) {
      return text;
    }

    const truncated = text.substring(0, maxLength);
    const lastSpace = truncated.lastIndexOf(' ');

    return lastSpace > 0 ? truncated.substring(0, lastSpace) + '...' : truncated + '...';
  }
}
