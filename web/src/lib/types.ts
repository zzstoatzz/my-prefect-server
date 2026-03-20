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
