import type { SectionAccent, SectionPriority } from '$lib/server/briefing';

/*
 * tailwind safelist (JIT needs to see these classes statically):
 * border-red-500 border-amber-500 border-emerald-500 border-sky-500 border-violet-500
 * text-red-400 text-amber-400 text-emerald-400 text-sky-400 text-violet-400
 * text-red-300 text-amber-300 text-emerald-300 text-sky-300 text-violet-300
 * bg-red-950/40 bg-amber-950/40 bg-emerald-950/40 bg-sky-950/40 bg-violet-950/40
 * bg-red-400 bg-amber-400 bg-emerald-400 bg-sky-400 bg-violet-400
 * border-l-2 border-l-3 border-l-4
 */

export const ACCENT_STYLES: Record<
	SectionAccent,
	{
		border: string;
		headerText: string;
		summaryText: string;
		dot: string;
		bgTint: string;
	}
> = {
	red: {
		border: 'border-red-500',
		headerText: 'text-red-400',
		summaryText: 'text-red-300/70',
		dot: 'bg-red-400',
		bgTint: 'bg-red-950/40'
	},
	amber: {
		border: 'border-amber-500',
		headerText: 'text-amber-400',
		summaryText: 'text-amber-300/70',
		dot: 'bg-amber-400',
		bgTint: 'bg-amber-950/40'
	},
	emerald: {
		border: 'border-emerald-500',
		headerText: 'text-emerald-400',
		summaryText: 'text-emerald-300/70',
		dot: 'bg-emerald-400',
		bgTint: 'bg-emerald-950/40'
	},
	sky: {
		border: 'border-sky-500',
		headerText: 'text-sky-400',
		summaryText: 'text-sky-300/70',
		dot: 'bg-sky-400',
		bgTint: 'bg-sky-950/40'
	},
	violet: {
		border: 'border-violet-500',
		headerText: 'text-violet-400',
		summaryText: 'text-violet-300/70',
		dot: 'bg-violet-400',
		bgTint: 'bg-violet-950/40'
	}
};

export const PRIORITY_LAYOUT: Record<
	SectionPriority,
	{ titleSize: string; padding: string; borderWidth: string }
> = {
	high: {
		titleSize: 'text-base',
		padding: 'px-5 py-4',
		borderWidth: 'border-l-4'
	},
	normal: {
		titleSize: 'text-sm',
		padding: 'px-5 py-4',
		borderWidth: 'border-l-3'
	},
	low: {
		titleSize: 'text-xs',
		padding: 'px-4 py-3',
		borderWidth: 'border-l-2'
	}
};
