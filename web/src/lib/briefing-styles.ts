import type { SectionAccent, SectionIcon, SectionPriority } from '$lib/server/briefing';

/*
 * tailwind safelist (JIT needs to see these classes statically):
 * border-red-500 border-amber-500 border-emerald-500 border-sky-500 border-violet-500
 * text-red-400 text-amber-400 text-emerald-400 text-sky-400 text-violet-400
 * text-red-300 text-amber-300 text-emerald-300 text-sky-300 text-violet-300
 * bg-red-950/20 bg-amber-950/20 bg-emerald-950/20 bg-sky-950/20 bg-violet-950/20
 * border-l-2 border-l-3 border-l-4
 * sm:col-span-2
 */

export const ACCENT_STYLES: Record<
	SectionAccent,
	{
		border: string;
		headerText: string;
		summaryText: string;
		highlightBar: string;
		bgTint: string;
	}
> = {
	red: {
		border: 'border-red-500',
		headerText: 'text-red-400',
		summaryText: 'text-red-300/70',
		highlightBar: 'border-red-500',
		bgTint: 'bg-red-950/20'
	},
	amber: {
		border: 'border-amber-500',
		headerText: 'text-amber-400',
		summaryText: 'text-amber-300/70',
		highlightBar: 'border-amber-500',
		bgTint: 'bg-amber-950/20'
	},
	emerald: {
		border: 'border-emerald-500',
		headerText: 'text-emerald-400',
		summaryText: 'text-emerald-300/70',
		highlightBar: 'border-emerald-500',
		bgTint: 'bg-emerald-950/20'
	},
	sky: {
		border: 'border-sky-500',
		headerText: 'text-sky-400',
		summaryText: 'text-sky-300/70',
		highlightBar: 'border-sky-500',
		bgTint: 'bg-sky-950/20'
	},
	violet: {
		border: 'border-violet-500',
		headerText: 'text-violet-400',
		summaryText: 'text-violet-300/70',
		highlightBar: 'border-violet-500',
		bgTint: 'bg-violet-950/20'
	}
};

/** heroicons mini 20x20 SVG paths */
export const ICON_PATHS: Record<SectionIcon, string> = {
	alert: 'M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
	clock: 'M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm.75-13a.75.75 0 0 0-1.5 0v5c0 .414.336.75.75.75h4a.75.75 0 0 0 0-1.5h-3.25V5z',
	check: 'M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm3.857-9.809a.75.75 0 0 0-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 1 0-1.06 1.061l2.5 2.5a.75.75 0 0 0 1.137-.089l4-5.5z',
	eye: 'M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z M.664 10.59a1.651 1.651 0 0 1 0-1.186A10.004 10.004 0 0 1 10 3c4.257 0 7.893 2.66 9.336 6.41.147.381.146.804 0 1.186A10.004 10.004 0 0 1 10 17c-4.257 0-7.893-2.66-9.336-6.41z',
	bolt: 'M11.983 1.907a.75.75 0 0 0-1.292-.657l-8.5 9.5A.75.75 0 0 0 2.75 12h6.572l-1.305 6.093a.75.75 0 0 0 1.292.657l8.5-9.5A.75.75 0 0 0 17.25 8h-6.572l1.305-6.093z',
	bookmark:
		'M10 2c-1.543 0-3.29.47-4.593 1.19C4.11 3.91 3 4.963 3 6.5c0 1.14.523 2.044 1.268 2.724.672.612 1.538 1.075 2.303 1.465A20.1 20.1 0 0 0 8 11.448V19.5a.75.75 0 0 0 1.272.537L10 19.31l.728.727A.75.75 0 0 0 12 19.5v-8.052c.56-.264 1.245-.622 1.929-1.06.765-.39 1.631-.853 2.303-1.465C16.977 8.544 17.5 7.64 17.5 6.5c0-1.537-1.11-2.59-2.407-3.31C13.79 2.47 12.043 2 10 2z'
};

export const PRIORITY_LAYOUT: Record<
	SectionPriority,
	{ colSpan: string; titleSize: string; padding: string; borderWidth: string }
> = {
	high: {
		colSpan: 'sm:col-span-2',
		titleSize: 'text-base',
		padding: 'px-5 py-4',
		borderWidth: 'border-l-4'
	},
	normal: {
		colSpan: '',
		titleSize: 'text-sm',
		padding: 'px-5 py-4',
		borderWidth: 'border-l-3'
	},
	low: {
		colSpan: '',
		titleSize: 'text-xs',
		padding: 'px-4 py-3',
		borderWidth: 'border-l-2'
	}
};
