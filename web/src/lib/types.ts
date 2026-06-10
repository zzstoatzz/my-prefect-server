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
	total_all: number;
	requests_24h: number;
	by_flow_7d: SpendByFlow[];
	recent: SpendRecentRun[];
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
