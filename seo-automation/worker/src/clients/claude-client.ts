import Anthropic from '@anthropic-ai/sdk';
import type { SEOIssue } from '../../../shared/types/seo-issues';
import { SEO_SYSTEM_PROMPT, getToneModifier, type ToneType } from '../prompts/seo-system-prompt';

export interface PageContent {
  title?: string;
  description?: string;
  h1?: string;
}

export interface AIRewriteResult {
  title: string;
  description: string;
  h1: string;
  ogTitle: string;
  ogDescription: string;
  altTexts: string[];
  schemaType: string;
  usage: TokenUsage;
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheCreationTokens: number;
  cacheReadTokens: number;
}

/**
 * Claude API Client with Prompt Caching
 *
 * Leverages Claude 3.5 Sonnet with prompt caching for 90% cost reduction.
 * System prompts are cached for 5 minutes, reducing cost to $0.005/page.
 */
export class ClaudeClient {
  private client: Anthropic;

  constructor(apiKey: string) {
    this.client = new Anthropic({ apiKey });
  }

  /**
   * Generate SEO-optimized content for a page
   */
  async generateSEOFixes(
    url: string,
    currentContent: PageContent,
    issues: SEOIssue[],
    tone?: ToneType
  ): Promise<AIRewriteResult> {
    const userPrompt = this.buildUserPrompt(url, currentContent, issues);

    const response = await this.client.messages.create({
      model: 'claude-3-5-sonnet-20241022',
      max_tokens: 500,
      system: [
        {
          type: 'text',
          text: SEO_SYSTEM_PROMPT + getToneModifier(tone),
          cache_control: { type: 'ephemeral' }, // 90% discount for 5min cache
        },
      ],
      messages: [
        { role: 'user', content: userPrompt },
      ],
    });

    const contentBlock = response.content[0];
    if (contentBlock.type !== 'text') {
      throw new Error('Unexpected response type from Claude');
    }

    // Extract JSON from response (handle potential markdown code blocks)
    let jsonText = contentBlock.text.trim();
    if (jsonText.startsWith('```json')) {
      jsonText = jsonText.replace(/```json\n/, '').replace(/\n```$/, '');
    } else if (jsonText.startsWith('```')) {
      jsonText = jsonText.replace(/```\n/, '').replace(/\n```$/, '');
    }

    const parsed = JSON.parse(jsonText);

    return {
      ...parsed,
      usage: {
        inputTokens: response.usage.input_tokens,
        outputTokens: response.usage.output_tokens,
        cacheCreationTokens: response.usage.cache_creation_input_tokens || 0,
        cacheReadTokens: response.usage.cache_read_input_tokens || 0,
      },
    };
  }

  /**
   * Calculate cost in USD based on token usage
   */
  calculateCost(usage: TokenUsage): number {
    const inputCost = (usage.inputTokens / 1_000_000) * 3.0;
    const outputCost = (usage.outputTokens / 1_000_000) * 15.0;
    const cacheCost = (usage.cacheCreationTokens / 1_000_000) * 3.75; // 25% premium
    const cacheReadCost = (usage.cacheReadTokens / 1_000_000) * 0.3; // 90% discount

    return inputCost + outputCost + cacheCost + cacheReadCost;
  }

  /**
   * Build user prompt from current content and issues
   */
  private buildUserPrompt(url: string, currentContent: PageContent, issues: SEOIssue[]): string {
    return `URL: ${url}

CURRENT CONTENT:
Title: ${currentContent.title || 'Missing'}
Description: ${currentContent.description || 'Missing'}
H1: ${currentContent.h1 || 'Missing'}

ISSUES DETECTED:
${issues.map((i) => `- ${i.title}: ${i.suggestion}`).join('\n')}

Generate optimized SEO metadata addressing these issues. Return valid JSON only.`;
  }
}
