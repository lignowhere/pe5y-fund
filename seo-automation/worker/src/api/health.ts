import { Hono } from 'hono';
import type { Bindings } from '../../../shared/types';

const health = new Hono<{ Bindings: Bindings }>();

health.get('/', async (c) => {
  try {
    // Test D1 connection
    const dbCheck = await c.env.DB.prepare('SELECT 1 as test').first();

    return c.json({
      status: 'healthy',
      timestamp: new Date().toISOString(),
      environment: c.env.ENVIRONMENT || 'production',
      checks: {
        database: dbCheck?.test === 1 ? 'ok' : 'error',
        worker: 'ok',
      },
    });
  } catch (error) {
    return c.json(
      {
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        error: error instanceof Error ? error.message : 'Unknown error',
      },
      500
    );
  }
});

export default health;