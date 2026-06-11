import { readFile, stat } from 'fs/promises';
import type { SpendSummary } from '$lib/types';

interface SpendEvent {
	id?: string;
	recorded_at?: string;
	flow_name?: string;
	task_name?: string;
	model?: string;
	request_count?: number;
	input_tokens?: number;
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
	recent: []
};

let cachedKey = '';
let cachedSpend: SpendSummary = emptySpend;

function numberValue(value: unknown): number {
	if (typeof value === 'number' && Number.isFinite(value)) return value;
	if (typeof value === 'string') {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : 0;
	}
	return 0;
}

function readEvent(line: string): SpendEvent | null {
	try {
		const event = JSON.parse(line) as SpendEvent;
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

	for (const event of byId.values()) {
		const timestamp = Date.parse(event.recorded_at ?? '');
		if (!Number.isFinite(timestamp)) continue;

		const cost = numberValue(event.total_cost_usd);
		const requests = numberValue(event.request_count);
		total_all += cost;
		requests_all += requests;
		addFlowRow(byFlowAll, event, cost, requests);
		recentRows.push({ ...event, timestamp });

		if (timestamp >= dayAgo) {
			total_24h += cost;
			requests_24h += requests;
			addFlowRow(byFlow24h, event, cost, requests);
		}

		if (timestamp >= weekAgo) {
			total_7d += cost;
			requests_7d += requests;
			addFlowRow(byFlow7d, event, cost, requests);
		}

		if (timestamp >= monthAgo) {
			total_30d += cost;
			requests_30d += requests;
			addFlowRow(byFlow30d, event, cost, requests);
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
