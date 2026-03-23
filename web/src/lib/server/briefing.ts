import { readFile } from 'fs/promises';

export type SectionAccent = 'red' | 'amber' | 'emerald' | 'sky' | 'violet';
export type SectionIcon = 'alert' | 'clock' | 'check' | 'eye' | 'bolt' | 'bookmark';
export type SectionPriority = 'high' | 'normal' | 'low';

export interface BriefingItem {
	item_id: string;
	note: string;
	highlight?: boolean;
}
export interface BriefingSection {
	title: string;
	summary: string;
	items: BriefingItem[];
	accent?: SectionAccent;
	icon?: SectionIcon;
	priority?: SectionPriority;
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
