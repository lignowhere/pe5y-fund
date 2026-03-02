import Anthropic from '@anthropic-ai/sdk';
import type { SEOContext, SEOContent } from '../../../shared/types';

export class ClaudeClient {
  private client: Anthropic;

  constructor(apiKey: string) {
    this.client = new Anthropic({ apiKey });
  }

  async generateSEOContent(
    currentContent: string,
    context: SEOContext
  ): Promise<SEOContent> {
    try {
      const response = await this.client.messages.create({
        model: 'claude-3-5-sonnet-20241022',
        max_tokens: 800,
        system: [
          {
            type: 'text',
            text: this.buildSystemPrompt(context.tone),
            cache_control: { type: 'ephemeral' }, // 90% cost reduction
          },
        ],
        messages: [
          {
            role: 'user',
            content: `Analyze and optimize this content for SEO:\n\nURL: ${context.url}\nCurrent Content: ${currentContent}\n\nProvide optimized SEO metadata in JSON format.`,
          },
        ],
      });

      // TODO Phase 04: Parse Claude response and extract structured SEO data
      console.log('🤖 Claude API response:', response);

      // Placeholder return
      throw new Error('SEO content generation pending Phase 04 implementation');
    } catch (error) {
      console.error('Claude API error:', error);
      throw error;
    }
  }

  private buildSystemPrompt(tone: string): string {
    const toneGuidelines = {
      'professional': 'Use formal business language with expert authority.',
      'casual': 'Use conversational, friendly language.',
      'seo-heavy': 'Prioritize keyword density and search optimization. Use primary keywords in first 50 characters.',
      'minimalist': 'Use concise, clear language without fluff.',
    };

    return `You are an SEO expert specializing in 2025 best practices. Generate optimized metadata following these guidelines:

TONE: ${toneGuidelines[tone as keyof typeof toneGuidelines] || toneGuidelines['seo-heavy']}

RULES:
- Title: 50-60 chars, primary keyword near start, match H1
- Meta description: 150-160 chars, include primary keyword
- H1: Single clear headline with main keyword
- Open Graph: Include og:title, og:description (200 chars max), og:type
- Alt texts: Descriptive, keyword-relevant, 125 chars max

OUTPUT FORMAT: JSON with keys: title, description, h1, ogTags, altTexts`;
  }
}