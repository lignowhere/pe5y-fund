-- SEO Automation Tool - D1 Database Schema
-- Stores only last 2 scans for lightweight operation

CREATE TABLE IF NOT EXISTS scan_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  json_report TEXT NOT NULL,
  diff_from_previous TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS issues_current (
  issue_id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  category TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('critical', 'high', 'medium', 'low')),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  current_value TEXT,
  expected_value TEXT,
  suggestion TEXT NOT NULL,
  ai_fix TEXT,
  priority_score INTEGER DEFAULT 0,
  detected_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Raw crawl data table (Phase 02)
CREATE TABLE IF NOT EXISTS raw_crawl_data (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  html TEXT NOT NULL,
  headers TEXT,
  console_logs TEXT,
  errors TEXT,
  status_code INTEGER,
  load_time_ms INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- AI metadata tracking table (Phase 04)
CREATE TABLE IF NOT EXISTS ai_metadata (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_timestamp TEXT NOT NULL DEFAULT (datetime('now')),
  total_tokens INTEGER,
  cached_tokens INTEGER,
  cost_usd REAL,
  pages_processed INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_scan_timestamp ON scan_state(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_scan_url ON scan_state(url);
CREATE INDEX IF NOT EXISTS idx_issues_severity ON issues_current(severity);
CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues_current(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_crawl_url ON raw_crawl_data(url, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ai_timestamp ON ai_metadata(run_timestamp DESC);