import { readFile } from 'fs/promises';

export interface BriefingItem {
	item_id: string;
	note: string;
}
export interface BriefingSection {
	title: string;
	summary: string;
	items: BriefingItem[];
}
export interface Briefing {
	headline: string;
	sections: BriefingSection[];
	generated_at: string;
}

export async function loadBriefing(): Promise<Briefing | null> {
	try {
		const raw = await readFile('/analytics/briefing.json', 'utf-8');
		return JSON.parse(raw);
	} catch {
		return null;
	}
}
