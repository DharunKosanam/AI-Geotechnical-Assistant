import OpenAI from "openai";
import { readFileSync } from "fs";
import { join } from "path";

// Force load from .env file to override system environment variables
function loadEnvFromFile() {
  try {
    const envPath = join(process.cwd(), ".env");
    const envContent = readFileSync(envPath, "utf-8");
    const lines = envContent.split("\n");
    
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith("#")) {
        const [key, ...valueParts] = trimmed.split("=");
        if (key && valueParts.length > 0) {
          const value = valueParts.join("=").trim();
          if (key === "OPENAI_API_KEY") {
            process.env[key] = value;
            console.log(`[OpenAI] Loaded API key from .env: ${value.substring(0, 12)}...`);
            return value;
          }
        }
      }
    }
  } catch (envError) {
    // .env file might not exist - this is OK when using Python backend
    console.warn('[OpenAI] No root .env file found (OK if using Python backend)');
  }
  return null;
}

// Load from file first to override system env vars
const fileApiKey = loadEnvFromFile();

// Use file key if available, otherwise fall back to process.env
const apiKey = fileApiKey || process.env.OPENAI_API_KEY;

if (!apiKey) {
  // Don't crash the server - just warn. Some routes (thread history) use MongoDB only
  // and don't need OpenAI. Only operations that actually call OpenAI will fail gracefully.
  console.warn(
    "[OpenAI] OPENAI_API_KEY is not set. OpenAI-dependent features will be unavailable. " +
    "Thread management via MongoDB will still work."
  );
}

// Log which key is being used for debugging
if (apiKey && process.env.NODE_ENV === 'development') {
  const keySource = fileApiKey ? "file (.env)" : "environment variable";
  console.log(`[OpenAI] Using API key from ${keySource}, starting with: ${apiKey.substring(0, 12)}...`);
}

// Export openai client - may be null if no API key is configured
export const openai: OpenAI | null = apiKey
  ? new OpenAI({ apiKey })
  : null;

