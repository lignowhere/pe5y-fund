import { useQuery } from '@tanstack/react-query';
import axios from 'axios';

interface HealthCheck {
  status: string;
  timestamp: string;
  environment: string;
  checks: {
    database: string;
    worker: string;
  };
}

const Dashboard = () => {
  const { data: health, isLoading } = useQuery<HealthCheck>({
    queryKey: ['health'],
    queryFn: async () => {
      const response = await axios.get('/health');
      return response.data;
    },
    refetchInterval: 30000, // Poll every 30s
  });

  return (
    <div style={{ padding: '2rem' }}>
      <h1>SEO Automation Dashboard</h1>
      <p style={{ color: '#666', marginBottom: '2rem' }}>
        Phase 01: Foundation - Infrastructure Setup Complete
      </p>

      <div style={{
        background: 'white',
        padding: '1.5rem',
        borderRadius: '8px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
        marginBottom: '1.5rem'
      }}>
        <h2>System Status</h2>
        {isLoading ? (
          <p>Loading...</p>
        ) : (
          <div style={{ marginTop: '1rem' }}>
            <p><strong>Status:</strong> {health?.status}</p>
            <p><strong>Environment:</strong> {health?.environment}</p>
            <p><strong>Database:</strong> {health?.checks.database}</p>
            <p><strong>Worker:</strong> {health?.checks.worker}</p>
            <p style={{ color: '#888', fontSize: '0.875rem', marginTop: '0.5rem' }}>
              Last checked: {health?.timestamp}
            </p>
          </div>
        )}
      </div>

      <div style={{
        background: 'white',
        padding: '1.5rem',
        borderRadius: '8px',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
      }}>
        <h2>Implementation Progress</h2>
        <ul style={{ marginTop: '1rem', lineHeight: '1.8' }}>
          <li>✅ Cloudflare Workers setup</li>
          <li>✅ D1 Database schema created</li>
          <li>✅ Hono API framework configured</li>
          <li>✅ Durable Objects setup</li>
          <li>✅ Health check endpoint</li>
          <li>✅ React dashboard initialized</li>
          <li>⏳ Phase 02: Crawler Agent (pending)</li>
          <li>⏳ Phase 03: SEO Analyzer (pending)</li>
        </ul>
      </div>
    </div>
  );
};

export default Dashboard;