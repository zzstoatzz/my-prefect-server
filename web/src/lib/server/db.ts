import { DuckDBInstance } from '@duckdb/node-api';
import { copyFileSync, statSync } from 'fs';

const srcPath = process.env.DUCKDB_PATH ?? '/analytics/analytics.duckdb';
const snapPath = '/tmp/hub_analytics_snapshot.duckdb';

let instance: DuckDBInstance | null = null;
let snapMtime = 0;

async function getInstance(): Promise<DuckDBInstance> {
	// refresh snapshot whenever the source file has changed
	let srcMtime = 0;
	try {
		srcMtime = statSync(srcPath).mtimeMs;
	} catch {
		throw new Error(`analytics db not found at ${srcPath}`);
	}

	if (!instance || srcMtime > snapMtime) {
		instance = null;
		// flock() is advisory on Linux — copyFileSync reads through the write lock
		copyFileSync(srcPath, snapPath);
		snapMtime = srcMtime;
		instance = await DuckDBInstance.create(snapPath, { access_mode: 'READ_ONLY' });
	}

	return instance;
}

export async function query<T = Record<string, unknown>>(sql: string): Promise<T[]> {
	const db = await getInstance();
	const conn = await db.connect();
	try {
		const reader = await conn.runAndReadAll(sql);
		return reader.getRowObjects() as T[];
	} finally {
		conn.closeSync();
	}
}
