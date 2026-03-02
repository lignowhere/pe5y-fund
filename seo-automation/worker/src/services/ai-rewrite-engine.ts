import type { SEOAnalysisResult, SEOIssue } from '../../../shared/types/seo-issues';
import { ClaudeClient, type AIRewriteResult, type PageContent, type TokenUsage } from '../clients/claude-client';
import { AIOutputValidator } from '../validators/ai-output-validator';
import type { ToneType } from '../prompts/seo-system-prompt';

export interface AIRewriteEngineResult {
  url: string;
  aiContent: AIRewriteResult;
  validation: {
    valid: boolean;
    errors: string[];
    warnings: string[];
  };
  cost: number;
  usage: TokenUsage;
}

/**
 * AI Rewrite Engine
 *
 * Orchestrates Claude API to generate SEO-optimized content for pages with issues.
 * Validates output and tracks cost.
 */
export class AIRewriteEngine {
  private claudeClient: ClaudeClient;
  private validator: AIOutputValidator;

  constructor(anthropicApiKey: string) {
    this.claudeClient = new ClaudeClient(anthropicApiKey);
    this.validator = new AIOutputValidator();
  }

  /**
   * Generate AI-powered SEO fixes for analysis results
   */
  async generateFixes(
    analysisResult: SEOAnalysisResult,
    tone?: ToneType
  ): Promise<AIRewriteEngineResult> {
    const startTime = Date.now();

    // Extract current page content from metadata
    const currentContent: PageContent = {
      title: analysisResult.metadata.title,
      description: analysisResult.metadata.description,
      h1: analysisResult.metadata.h1,
    };

    // Filter issues that need AI fixes (meta, header, accessibility)
    const relevantIssues = analysisResult.issues.filter((issue) =>
      ['meta', 'header', 'accessibility'].includes(issue.category)
    );

    if (relevantIssues.length === 0) {
      console.log('No issues requiring AI fixes');
      return {
        url: analysisResult.url,
        aiContent: this.getDefaultContent(currentContent),
        validation: { valid: true, errors: [], warnings: [] },
        cost: 0,
        usage: {
          inputTokens: 0,
          outputTokens: 0,
          cacheCreationTokens: 0,
          cacheReadTokens: 0,
        },
      };
    }

    // Generate AI fixes
    let aiContent = await this.claudeClient.generateSEOFixes(
      analysisResult.url,
      currentContent,
      relevantIssues,
      tone
    );

    // Validate output
    const validation = this.validator.validate(aiContent);

    // Auto-fix if validation failed
    if (!validation.valid) {
      console.log(`⚠️ Validation failed for ${analysisResult.url}, applying auto-fix`);
      aiContent = this.validator.autoFix(aiContent);
    }

    // Calculate cost
    const cost = this.claudeClient.calculateCost(aiContent.usage);

    const duration = Date.now() - startTime;
    console.log(`🤖 AI generated fixes for ${analysisResult.url} in ${duration}ms (cost: $${cost.toFixed(4)})`);

    return {
      url: analysisResult.url,
      aiContent,
      validation,
      cost,
      usage: aiContent.usage,
    };
  }

  /**
   * Get default content when no AI generation needed
   */
  private getDefaultContent(current: PageContent): AIRewriteResult {
    return {
      title: current.title || '',
      description: current.description || '',
      h1: current.h1 || '',
      ogTitle: current.title || '',
      ogDescription: current.description || '',
      altTexts: [],
      schemaType: 'WebPage',
      usage: {
        inputTokens: 0,
        outputTokens: 0,
        cacheCreationTokens: 0,
        cacheReadTokens: 0,
      },
    };
  }

  /**
   * Format AI content as JSON string for storage
   */
  formatForStorage(result: AIRewriteEngineResult): string {
    return JSON.stringify({
      title: result.aiContent.title,
      description: result.aiContent.description,
      h1: result.aiContent.h1,
      ogTitle: result.aiContent.ogTitle,
      ogDescription: result.aiContent.ogDescription,
      altTexts: result.aiContent.altTexts,
      schemaType: result.aiContent.schemaType,
      cost: result.cost,
      validation: result.validation,
    }, null, 2);
  }
}
