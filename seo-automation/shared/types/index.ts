// Shared TypeScript types for SEO Automation Tool

export interface Bindings {
  DB: D1Database;
  SITE_CRAWLER: DurableObjectNamespace;
  MYBROWSER?: Fetcher; // Optional: only available in remote/production
  ANTHROPIC_API_KEY?: string;
  SCRAPINGBEE_API_KEY?: string;
  ENVIRONMENT: string;
}

export interface ScanState {
  id: number;
  url: string;
  timestamp: string;
  json_report: string;
  diff_from_previous: string | null;
  created_at: string;
}

export interface Issue {
  issue_id: string;
  category: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  suggestion: string;
  ai_fix: string | null;
  priority_score: number;
  created_at: string;
}

export interface PageData {
  html: string;
  headers: Record<string, string>;
  console: string[];
  errors: string[];
  loadTime?: number;
  statusCode?: number;
}

export interface SEOContext {
  url: string;
  industry?: string;
  tone: 'professional' | 'casual' | 'seo-heavy' | 'minimalist';
}

export interface SEOContent {
  title: string;
  description: string;
  h1: string;
  ogTags: Record<string, string>;
  altTexts: string[];
}

export interface ScanReport {
  url: string;
  timestamp: string;
  issues: Issue[];
  seoContent: SEOContent;
  performance: {
    loadTime: number;
    consoleErrors: number;
  };
}

export interface DeltaComparison {
  newIssues: Issue[];
  resolvedIssues: Issue[];
  worsenedIssues: Issue[];
  improvedIssues: Issue[];
  unchangedIssues: Issue[];
}