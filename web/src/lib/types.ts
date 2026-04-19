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
