/**
 * A budget for concurrent EventSource connections.
 *
 * Browsers cap concurrent connections per origin - 6 over HTTP/1.1, which is
 * what SongSeek gets when served over plain HTTP. An SSE connection is held
 * open for as long as it streams, so these are not ordinary requests that queue
 * and drain: each one permanently occupies a slot.
 *
 * Progress streams are opened per transferring task, so a single album
 * downloading eight tracks can hold more connections than the browser allows.
 * Everything else on the page - queries, polls, even navigating somewhere less
 * broken - then queues behind them and never starts. It looks exactly like the
 * server hanging.
 *
 * Streams therefore take a slot, and callers refused one fall back to polled
 * progress, which every consumer already renders while no live value has
 * arrived. The cap sits well under six because the app still needs connections
 * for the library-activity stream, ordinary queries, and images.
 *
 * Waiting is a callback queue rather than reactive state on purpose. If callers
 * tracked the budget reactively, every release would invalidate every waiting
 * effect - and because those effects tear down their stream on cleanup before
 * re-running, one stream ending would churn all the others.
 */
const MAX_CONCURRENT_STREAMS = 2;

let held = 0;
const waiting: Array<() => void> = [];

/**
 * Takes a slot if one is free. When refused, `onAvailable` is queued and called
 * once, in arrival order, as soon as a slot frees - the caller then asks again.
 */
export function acquireStreamSlot(onAvailable?: () => void): boolean {
	if (held >= MAX_CONCURRENT_STREAMS) {
		if (onAvailable && !waiting.includes(onAvailable)) waiting.push(onAvailable);
		return false;
	}
	held += 1;
	return true;
}

export function releaseStreamSlot(): void {
	if (held === 0) return;
	held -= 1;
	waiting.shift()?.();
}

/** A caller that no longer wants a stream must stop waiting for one. */
export function cancelStreamSlotWait(onAvailable: () => void): void {
	const at = waiting.indexOf(onAvailable);
	if (at !== -1) waiting.splice(at, 1);
}

/** Test seams. */
export function streamSlotsInUse(): number {
	return held;
}

export function resetStreamSlots(): void {
	held = 0;
	waiting.length = 0;
}

export { MAX_CONCURRENT_STREAMS };
