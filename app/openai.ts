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
    // .env file might not exist
    console.error('[OpenAI] Could not read .env file');
  }
  return null;
}

// Load from file first to override system env vars
const fileApiKey = loadEnvFromFile();

// Use file key if available, otherwise fall back to process.env
const apiKey = fileApiKey || process.env.OPENAI_API_KEY;

if (!apiKey) {
  throw new Error(
    "OPENAI_API_KEY is not set. Please add it to your .env file."
  );
}

// Log which key is being used for debugging
if (process.env.NODE_ENV === 'development') {
  const keySource = fileApiKey ? "file (.env)" : "environment variable";
  console.log(`[OpenAI] Using API key from ${keySource}, starting with: ${apiKey.substring(0, 12)}...`);
}

export const openai = new OpenAI({
  apiKey: apiKey,
});

