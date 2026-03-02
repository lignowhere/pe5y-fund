import { Hono } from 'hono';
import type { Bindings, ScanState } from '../../../shared/types';

const scans = new Hono<{ Bindings: Bindings }>();

scans.get('/latest', async (c) => {
  try {
    const results = await c.env.DB.prepare(
      'SELECT * FROM scan_state ORDER BY timestamp DESC LIMIT 2'
    ).all<ScanState>();

    return c.json({
      scans: results.results || [],
      count: results.results?.length || 0,
    });
  } catch (error) {
    return c.json(
      {
        error: error instanceof Error ? error.message : 'Failed to fetch scans',
      },
      500
    );
  }
});

scans.get('/issues', async (c) => {
  try {
    const results = await c.env.DB.prepare(
      'SELECT * FROM issues_current ORDER BY priority_score DESC'
    ).all();

    return c.json({
      issues: results.results || [],
      count: results.results?.length || 0,
    });
  } catch (error) {
    return c.json(
      {
        error: error instanceof Error ? error.message : 'Failed to fetch issues',
      },
      500
    );
  }
});

export default scans;