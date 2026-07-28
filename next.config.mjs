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
        // GeoPilot workspace. afterFiles so the app/api/workspace/cpt/interpret
        // Route Handler (long-timeout upload) wins; everything else (e.g.
        // GET /api/workspace/status) proxies straight to FastAPI.
        { source: '/api/workspace/:path*', destination: `${PYTHON_API_URL}/api/workspace/:path*` },
        // /api/kb/* is handled by the dedicated long-timeout Route Handler
        // (app/api/kb/[...path]/route.ts), NOT this rewrite — the preflight + bulk
        // submission exceed the rewrite's short socket timeout.
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
