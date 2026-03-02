// SEO Issue Type Definitions

export enum IssueCategory {
  META = 'meta',
  HEADER = 'header',
  SCHEMA = 'schema',
  ASSET = 'asset',
  ACCESSIBILITY = 'accessibility',
  TRACKING = 'tracking',
  SITEMAP = 'sitemap',
  ROBOTS = 'robots',
  CLOUDFLARE = 'cloudflare',
}

export enum IssueSeverity {
  CRITICAL = 'critical',
  HIGH = 'high',
  MEDIUM = 'medium',
  LOW = 'low',
}

export interface SEOIssue {
  issueId: string;
  url: string;
  category: IssueCategory;
  severity: IssueSeverity;
  title: string;
  description: string;
  currentValue?: string;
  expectedValue?: string;
  suggestion: string;
  detectedAt: string;
}

export interface SEOAnalysisResult {
  url: string;
  timestamp: string;
  issues: SEOIssue[];
  summary: {
    totalIssues: number;
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  metadata: {
    title?: string;
    description?: string;
    h1?: string;
    totalImages: number;
    imagesWithAlt: number;
  };
}
