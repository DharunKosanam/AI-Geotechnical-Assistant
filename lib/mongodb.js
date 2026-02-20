import { MongoClient } from 'mongodb';

const options = {
  maxPoolSize: 10,
  minPoolSize: 2,
  maxIdleTimeMS: 30000,
};

let clientPromise;

function getClientPromise() {
  if (clientPromise) return clientPromise;

  const uri = process.env.MONGODB_URI;
  if (!uri) {
    throw new Error('Please add your MONGODB_URI to .env file');
  }

  if (process.env.NODE_ENV === 'development') {
    if (!global._mongoClientPromise) {
      console.log('🔌 Creating new MongoDB connection (development mode)');
      const client = new MongoClient(uri, options);
      global._mongoClientPromise = client.connect();
    }
    clientPromise = global._mongoClientPromise;
  } else {
    console.log('🔌 Creating MongoDB connection (production mode)');
    const client = new MongoClient(uri, options);
    clientPromise = client.connect();
  }

  return clientPromise;
}

export async function getDatabase() {
  const client = await getClientPromise();
  return client.db('ai-geotech-db');
}

export default getClientPromise;

