export interface Card {
	id: string;
	source: string;
	kind: string;
	title: string;
	url: string;
	score: number;
	updated: string;
	tags: string[];
	meta: Record<string, string | number>;
}

export interface DashboardStats {
	tracked: number;
	open: number;
	with_reactions: number;
	repos: number;
}

export interface SpendByFlow {
	flow_name: string;
	cost_usd: number;
	input_tokens: number;
	output_tokens: number;
	requests: number;
}

export interface SpendRecentRun {
	recorded_at: string;
	flow_name: string;
	task_name: string;
	model: string;
	cost_usd: number;
	input_tokens: number;
	output_tokens: number;
}

export interface SpendSummary {
	total_24h: number;
	total_7d: number;
	total_30d: number;
	total_all: number;
	requests_24h: number;
	requests_7d: number;
	requests_30d: number;
	requests_all: number;
	by_flow_24h: SpendByFlow[];
	by_flow_7d: SpendByFlow[];
	by_flow_30d: SpendByFlow[];
	by_flow_all: SpendByFlow[];
	recent: SpendRecentRun[];
}

// infra costs — io.zzstoatzz.cost.snapshot, read from the public PDS.
// all amounts are integer USD cents.
export interface CostRollup {
	key: string;
	amount: number;
	estimated: boolean;
}

export interface CostLineItem {
	provider: string;
	project: string;
	service: string;
	amount: number;
	estimated: boolean;
	usage?: string;
	note?: string;
}

export interface InfraCostSnapshot {
	generatedAt: string;
	periodStart?: string;
	periodEnd?: string;
	currency: string;
	total: number;
	byProvider: CostRollup[];
	byProject: CostRollup[];
	lineItems: CostLineItem[];
}

export interface DiscoveryPoolPost {
	uri: string;
	text: string;
	liked_at: string;
}

export interface DiscoveryPoolEntry {
	handle: string;
	did: string;
	likes_in_window: number;
	last_liked_at: string;
	sample_posts: DiscoveryPoolPost[];
}
