import { Pool, PoolConfig } from 'pg';
import dotenv from 'dotenv';

dotenv.config();

const poolConfig: PoolConfig = {
  connectionString: process.env.DATABASE_URL,
  min: parseInt(process.env.POOL_MIN || '2'),
  max: parseInt(process.env.POOL_MAX || '10'),
  idleTimeoutMillis: parseInt(process.env.POOL_IDLE_TIMEOUT_MS || '30000'),
  connectionTimeoutMillis: 5000,
};

export const pool = new Pool(poolConfig);

// Warm pool on startup
pool.query('SELECT NOW()').then(() => {
  console.log('✓ Database pool initialized');
}).catch(err => {
  console.error('✗ Database pool error:', err);
  process.exit(1);
});

// Graceful shutdown
process.on('SIGTERM', () => {
  pool.end(() => {
    console.log('Database pool closed');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  pool.end(() => {
    console.log('Database pool closed');
    process.exit(0);
  });
});

export default pool;
