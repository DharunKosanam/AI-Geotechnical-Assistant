// Server-side target for the FastAPI backend. NON-public (no NEXT_PUBLIC_ prefix)
// so it is never shipped to the browser. The browser hits same-origin relative
// paths; Next.js rewrites() proxies them here server-side.
const PYTHON_API_URL = process.env.PYTHON_API_URL || 'http://localhost:8000';

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {},
  typescript: {
    ignoreBuildErrors: true,
  },
  async rewrites() {
    return {
      // beforeFiles: these paths collide with legacy OpenAI route handlers under
      // app/api/. In python mode they must reach FastAPI, so shadow the files.
      beforeFiles: [
        { source: '/api/assistants/:path*', destination: `${PYTHON_API_URL}/api/assistants/:path*` },
        { source: '/api/files/:path*',      destination: `${PYTHON_API_URL}/api/files/:path*` },
      ],
      afterFiles: [
        { source: '/auth/:path*',       destination: `${PYTHON_API_URL}/auth/:path*` },
        { source: '/chat/:path*',       destination: `${PYTHON_API_URL}/chat/:path*` },
        { source: '/api/upload/:path*', destination: `${PYTHON_API_URL}/api/upload/:path*` },
        { source: '/api/upload',        destination: `${PYTHON_API_URL}/api/upload` },
        { source: '/api/files',         destination: `${PYTHON_API_URL}/api/files` },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
