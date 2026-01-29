/** @type {import('next').NextConfig} */
const nextConfig = {
  // Environment variables are loaded from .env file
  env: {
    // Next.js automatically loads .env files
  },
  
  // Proxy API requests to Python FastAPI backend
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:8000/api/:path*',
      },
    ];
  },
};

export default nextConfig;
