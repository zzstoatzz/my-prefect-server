import { readFile, stat } from 'fs/promises';
import type { SpendSummary } from '$lib/types';

interface SpendEvent {
	id?: string;
	recorded_at?: string;
	flow_name?: string;
	task_name?: string;
	provider?: string;
	model?: string;
	request_count?: number;
	input_tokens?: number;
	cache_read_tokens?: number;
	cache_write_tokens?: number;
	output_tokens?: number;
	total_cost_usd?: number;
}

const spendLogPath = process.env.LLM_SPEND_LOG_PATH ?? '/analytics/llm-spend.jsonl';
const emptySpend: SpendSummary = {
	total_24h: 0,
	total_7d: 0,
	total_30d: 0,
	total_all: 0,
	requests_24h: 0,
	requests_7d: 0,
	requests_30d: 0,
	requests_all: 0,
	by_flow_24h: [],
	by_flow_7d: [],
	by_flow_30d: [],
	by_flow_all: [],
	by_model_24h: [],
	by_model_7d: [],
	by_model_30d: [],
	by_model_all: [],
	cache_24h: { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
	cache_7d: { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
	cache_30d: { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
	cache_all: { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
	recent: []
};

let cachedKey = '';
let cachedSpend: SpendSummary = emptySpend;

function numberValue(value: number | undefined): number {
	return value !== undefined && Number.isFinite(value) ? value : 0;
}

function readEvent(line: string): SpendEvent | null {
	try {
		const event: SpendEvent = JSON.parse(line);
		if (!event.recorded_at) return null;
		return event;
	} catch {
		return null;
	}
}

function summarize(events: SpendEvent[]): SpendSummary {
	const now = Date.now();
	const dayAgo = now - 24 * 60 * 60 * 1000;
	const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
	const monthAgo = now - 30 * 24 * 60 * 60 * 1000;
	const byId = new Map<string, SpendEvent>();

	for (const event of events) {
		const id = event.id ?? `${event.recorded_at}:${event.flow_name}:${event.task_name}:${event.model}`;
		byId.set(id, event);
	}

	type FlowRow = {
		flow_name: string;
		cost_usd: number;
		input_tokens: number;
		output_tokens: number;
		requests: number;
	};
	const byFlow24h = new Map<string, FlowRow>();
	const byFlow7d = new Map<string, FlowRow>();
	const byFlow30d = new Map<string, FlowRow>();
	const byFlowAll = new Map<string, FlowRow>();

	type ModelRow = { provider: string; model: string; cost_usd: number; requests: number };
	const byModel24h = new Map<string, ModelRow>();
	const byModel7d = new Map<string, ModelRow>();
	const byModel30d = new Map<string, ModelRow>();
	const byModelAll = new Map<string, ModelRow>();

	type CacheAcc = { input_tokens: number; cache_read_tokens: number; cache_write_tokens: number };
	const cache_24h: CacheAcc = { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
	const cache_7d: CacheAcc = { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
	const cache_30d: CacheAcc = { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
	const cache_all: CacheAcc = { input_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 };

	let total_24h = 0;
	let total_7d = 0;
	let total_30d = 0;
	let total_all = 0;
	let requests_24h = 0;
	let requests_7d = 0;
	let requests_30d = 0;
	let requests_all = 0;
	const recentRows: Array<SpendEvent & { timestamp: number }> = [];

	function addFlowRow(map: Map<string, FlowRow>, event: SpendEvent, cost: number, requests: number) {
		const flowName = event.flow_name?.trim() || 'unknown';
		const row = map.get(flowName) ?? {
			flow_name: flowName,
			cost_usd: 0,
			input_tokens: 0,
			output_tokens: 0,
			requests: 0
		};
		row.cost_usd += cost;
		row.input_tokens += numberValue(event.input_tokens);
		row.output_tokens += numberValue(event.output_tokens);
		row.requests += requests;
		map.set(flowName, row);
	}

	function topFlows(map: Map<string, FlowRow>): FlowRow[] {
		return [...map.values()].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 8);
	}

	function addModelRow(map: Map<string, ModelRow>, event: SpendEvent, cost: number, requests: number) {
		const provider = event.provider?.trim() || 'unknown';
		const model = event.model?.trim() || 'unknown';
		const key = `${provider}:${model}`;
		const row = map.get(key) ?? { provider, model, cost_usd: 0, requests: 0 };
		row.cost_usd += cost;
		row.requests += requests;
		map.set(key, row);
	}

	function topModels(map: Map<string, ModelRow>): ModelRow[] {
		return [...map.values()].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 6);
	}

	function addCache(acc: CacheAcc, event: SpendEvent) {
		acc.input_tokens += numberValue(event.input_tokens);
		acc.cache_read_tokens += numberValue(event.cache_read_tokens);
		acc.cache_write_tokens += numberValue(event.cache_write_tokens);
	}

	for (const event of byId.values()) {
		const timestamp = Date.parse(event.recorded_at ?? '');
		if (!Number.isFinite(timestamp)) continue;

		const cost = numberValue(event.total_cost_usd);
		const requests = numberValue(event.request_count);
		total_all += cost;
		requests_all += requests;
		addFlowRow(byFlowAll, event, cost, requests);
		addModelRow(byModelAll, event, cost, requests);
		addCache(cache_all, event);
		recentRows.push({ ...event, timestamp });

		if (timestamp >= dayAgo) {
			total_24h += cost;
			requests_24h += requests;
			addFlowRow(byFlow24h, event, cost, requests);
			addModelRow(byModel24h, event, cost, requests);
			addCache(cache_24h, event);
		}

		if (timestamp >= weekAgo) {
			total_7d += cost;
			requests_7d += requests;
			addFlowRow(byFlow7d, event, cost, requests);
			addModelRow(byModel7d, event, cost, requests);
			addCache(cache_7d, event);
		}

		if (timestamp >= monthAgo) {
			total_30d += cost;
			requests_30d += requests;
			addFlowRow(byFlow30d, event, cost, requests);
			addModelRow(byModel30d, event, cost, requests);
			addCache(cache_30d, event);
		}
	}

	return {
		total_24h,
		total_7d,
		total_30d,
		total_all,
		requests_24h,
		requests_7d,
		requests_30d,
		requests_all,
		by_flow_24h: topFlows(byFlow24h),
		by_flow_7d: topFlows(byFlow7d),
		by_flow_30d: topFlows(byFlow30d),
		by_flow_all: topFlows(byFlowAll),
		by_model_24h: topModels(byModel24h),
		by_model_7d: topModels(byModel7d),
		by_model_30d: topModels(byModel30d),
		by_model_all: topModels(byModelAll),
		cache_24h,
		cache_7d,
		cache_30d,
		cache_all,
		recent: recentRows
			.sort((a, b) => b.timestamp - a.timestamp)
			.slice(0, 12)
			.map((event) => ({
				recorded_at: event.recorded_at ?? '',
				flow_name: event.flow_name?.trim() || 'unknown',
				task_name: event.task_name ?? '',
				model: event.model ?? '',
				cost_usd: numberValue(event.total_cost_usd),
				input_tokens: numberValue(event.input_tokens),
				output_tokens: numberValue(event.output_tokens)
			}))
	};
}

export async function loadLiveSpendSummary(): Promise<SpendSummary> {
	try {
		const info = await stat(spendLogPath);
		const key = `${info.mtimeMs}:${info.size}`;
		if (key === cachedKey) return cachedSpend;

		const text = await readFile(spendLogPath, 'utf-8');
		cachedSpend = summarize(
			text
				.split('\n')
				.map(readEvent)
				.filter((event): event is SpendEvent => event !== null)
		);
		cachedKey = key;
		return cachedSpend;
	} catch {
		cachedKey = '';
		cachedSpend = emptySpend;
		return emptySpend;
	}
}
