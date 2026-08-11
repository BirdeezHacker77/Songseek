import { beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
	invalidate: vi.fn().mockResolvedValue(undefined),
	invalidateCatalog: vi.fn().mockResolvedValue(undefined)
}));

vi.mock('$lib/queries/QueryClient', () => ({
	invalidateQueriesWithPersister: h.invalidate
}));
vi.mock('./LibraryCatalogInvalidation', () => ({
	invalidateLibraryCatalog: h.invalidateCatalog
}));

import { LibraryQueryKeyFactory } from './LibraryQueryKeyFactory';
import { createLibraryActivityEvents } from './LibraryActivityEvents';

class FakeEventSource {
	static instances: FakeEventSource[] = [];
	readonly url: string;
	readonly listeners = new Map<string, Set<(event: Event) => void>>();
	closed = false;

	constructor(url: string | URL) {
		this.url = String(url);
		FakeEventSource.instances.push(this);
	}

	addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
		const callback = listener as (event: Event) => void;
		const listeners = this.listeners.get(type) ?? new Set<(event: Event) => void>();
		listeners.add(callback);
		this.listeners.set(type, listeners);
	}

	close(): void {
		this.closed = true;
	}

	emit(type: string, event: Event = new Event(type)): void {
		for (const listener of this.listeners.get(type) ?? []) listener(event);
	}
}

beforeEach(() => {
	vi.clearAllMocks();
	FakeEventSource.instances = [];
	vi.stubGlobal('EventSource', FakeEventSource);
});

describe('createLibraryActivityEvents', () => {
	it('re-reads durable state whenever either stream opens or reconnects', () => {
		const events = createLibraryActivityEvents();
		events.start(true);
		expect(FakeEventSource.instances).toHaveLength(2);

		FakeEventSource.instances[0].emit('open');
		expect(h.invalidate).toHaveBeenCalledWith({
			queryKey: LibraryQueryKeyFactory.activityPrefix()
		});

		h.invalidate.mockClear();
		FakeEventSource.instances[1].emit('open');
		expect(h.invalidate).toHaveBeenCalledWith({
			queryKey: LibraryQueryKeyFactory.operationsPrefix()
		});
		expect(h.invalidate).toHaveBeenCalledWith({
			queryKey: LibraryQueryKeyFactory.reviewsPrefix()
		});
		expect(h.invalidate).toHaveBeenCalledWith({
			queryKey: LibraryQueryKeyFactory.activityPrefix()
		});
	});

	it('uses SSE only as an invalidation signal and closes every replaced stream', () => {
		const events = createLibraryActivityEvents();
		events.start(true);
		const first = [...FakeEventSource.instances];
		first[1].emit('activity.changed');
		expect(h.invalidate).toHaveBeenCalledWith({
			queryKey: LibraryQueryKeyFactory.operationsPrefix()
		});

		events.start(false);
		expect(first.every((source) => source.closed)).toBe(true);
		expect(FakeEventSource.instances).toHaveLength(3);
		events.stop();
		expect(FakeEventSource.instances[2].closed).toBe(true);
	});

	it('sweeps catalog projections once when the durable catalog revision advances', () => {
		const events = createLibraryActivityEvents();
		events.start(true);
		const changed = new MessageEvent('activity.changed', {
			data: JSON.stringify({ revisions: { operation: 4, catalog: 9 } })
		});
		FakeEventSource.instances[0].emit('activity.changed', changed);
		FakeEventSource.instances[1].emit('activity.changed', changed);
		expect(h.invalidateCatalog).toHaveBeenCalledOnce();

		FakeEventSource.instances[0].emit(
			'activity.changed',
			new MessageEvent('activity.changed', {
				data: JSON.stringify({ revisions: { operation: 5, catalog: 10 } })
			})
		);
		expect(h.invalidateCatalog).toHaveBeenCalledTimes(2);
	});
});
