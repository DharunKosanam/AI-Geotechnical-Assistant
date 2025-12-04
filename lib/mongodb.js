import { MongoClient } from 'mongodb';

if (!process.env.MONGODB_URI) {
  throw new Error('Please add your MONGODB_URI to .env file');
}

const uri = process.env.MONGODB_URI;
const options = {
  maxPoolSize: 10, // Limit connection pool size
  minPoolSize: 2,  // Maintain minimum connections
  maxIdleTimeMS: 30000, // Close idle connections after 30s
};

let client;
let clientPromise;

if (process.env.NODE_ENV === 'development') {
  // In development mode, use a global variable so the connection is preserved across hot reloads
  // This prevents "too many connections" errors during Next.js Fast Refresh
  if (!global._mongoClientPromise) {
    console.log('🔌 Creating new MongoDB connection (development mode)');
    client = new MongoClient(uri, options);
    global._mongoClientPromise = client.connect();
  } else {
    console.log('♻️  Reusing existing MongoDB connection (development mode)');
  }
  clientPromise = global._mongoClientPromise;
} else {
  // In production mode, create a new client (connection pooling handles efficiency)
  console.log('🔌 Creating MongoDB connection (production mode)');
  client = new MongoClient(uri, options);
  clientPromise = client.connect();
}

// Export a function to get the database
export async function getDatabase() {
  const client = await clientPromise;
  return client.db('ai-geotech-db');
}

export default clientPromise;

