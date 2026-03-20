import type { Card } from '$lib/types';

const PALETTE = [
	'bg-red-900/60 text-red-300 border-red-700/40',
	'bg-orange-900/60 text-orange-300 border-orange-700/40',
	'bg-amber-900/60 text-amber-300 border-amber-700/40',
	'bg-emerald-900/60 text-emerald-300 border-emerald-700/40',
	'bg-teal-900/60 text-teal-300 border-teal-700/40',
	'bg-sky-900/60 text-sky-300 border-sky-700/40',
	'bg-violet-900/60 text-violet-300 border-violet-700/40',
	'bg-pink-900/60 text-pink-300 border-pink-700/40'
];

function hash(s: string): number {
	let h = 0;
	for (let i = 0; i < s.length; i++) {
		h = ((h << 5) - h + s.charCodeAt(i)) | 0;
	}
	return Math.abs(h);
}

export function hashColor(s: string): string {
	return PALETTE[hash(s) % PALETTE.length];
}

export function timeAgo(iso: string): string {
	const ms = Date.now() - new Date(iso).getTime();
	const sec = Math.floor(ms / 1000);
	if (sec < 60) return 'just now';
	const min = Math.floor(sec / 60);
	if (min < 60) return `${min}m ago`;
	const hr = Math.floor(min / 60);
	if (hr < 24) return `${hr}h ago`;
	const days = Math.floor(hr / 24);
	if (days < 30) return `${days}d ago`;
	const months = Math.floor(days / 30);
	if (months < 12) return `${months}mo ago`;
	return `${Math.floor(months / 12)}y ago`;
}

export interface TextSegment {
	text?: string;
	code?: string;
}

export function parseInlineCode(text: string): TextSegment[] {
	const segments: TextSegment[] = [];
	const re = /`([^`]+)`/g;
	let last = 0;
	let match: RegExpExecArray | null;
	while ((match = re.exec(text)) !== null) {
		if (match.index > last) {
			segments.push({ text: text.slice(last, match.index) });
		}
		segments.push({ code: match[1] });
		last = re.lastIndex;
	}
	if (last < text.length) {
		segments.push({ text: text.slice(last) });
	}
	return segments;
}

export function authorUrl(card: Card): string | null {
	const user = card.meta.user;
	if (!user) return null;
	switch (card.source) {
		case 'github':
			return `https://github.com/${user}`;
		case 'tangled':
			return `https://tangled.org/${user}`;
		default:
			return null;
	}
}

export function originUrl(card: Card): string | null {
	const repo = card.meta.repo;
	if (!repo) return null;
	switch (card.source) {
		case 'github':
			return `https://github.com/${repo}`;
		case 'tangled':
			return `https://tangled.org/zzstoatzz.io/${repo}`;
		default:
			return null;
	}
}
