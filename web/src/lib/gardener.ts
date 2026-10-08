import type { loadGardener, Work, Attempt } from './server/gardener';

export type Gardener = Awaited<ReturnType<typeof loadGardener>>;
export type { Work, Attempt } from './server/gardener';
export type WorkFilter = 'all' | 'attention' | 'working' | 'finished';

export function requestText(run: Work): string {
    return run.parameters.prompt ?? run.parameters.task ?? run.parameters.title ?? '';
}

export function workTitle(run: Work): string {
    const text = (run.parameters.title ?? requestText(run)).replace(/\s+/g, ' ').trim();
    if (!text) return run.name;
    const sentence = text.match(/^.{20,}?[.!?](?:\s|$)/)?.[0]?.trim() ?? text;
    return sentence.length > 130 ? `${sentence.slice(0, 127).trimEnd()}…` : sentence;
}

export function repository(run: Work): string | null {
    return run.parameters.workspace?.repo ?? run.parameters.repo ?? null;
}

export function isWorking(run: Work): boolean {
    return ['PENDING', 'RUNNING', 'SCHEDULED', 'CANCELLING'].includes(run.state_type ?? '');
}

export function needsAttention(run: Work, attempt?: Attempt): boolean {
    return ['FAILED', 'CRASHED', 'PAUSED'].includes(run.state_type ?? '') ||
        (attempt !== undefined && ['retained', 'missing'].includes(attempt.phase));
}

export function workStatus(run: Work): string {
    if (run.state_name && run.state_name.toUpperCase() !== run.state_type) return run.state_name;
    switch (run.state_type) {
        case 'COMPLETED': return 'Finished';
        case 'RUNNING': return 'Working';
        case 'PENDING': return 'Starting';
        case 'SCHEDULED': return 'Queued';
        case 'PAUSED': return 'Needs review';
        case 'FAILED': return 'Failed';
        case 'CRASHED': return 'Interrupted';
        case 'CANCELLING': return 'Stopping';
        case 'CANCELLED': return 'Cancelled';
        default: return 'Status unknown';
    }
}

export function duration(seconds: number | null): string {
    if (seconds === null) return 'Not recorded';
    const rounded = Math.round(seconds);
    return rounded < 60 ? `${rounded}s` : `${Math.floor(rounded / 60)}m ${rounded % 60}s`;
}
