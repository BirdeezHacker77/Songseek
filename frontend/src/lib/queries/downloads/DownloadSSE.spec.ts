import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class FakeEventSource {
	static instances: FakeEventSource[] = [];
	url: string;
	listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
	closed = false;

	constructor(url: string) {
		this.url = url;
		FakeEventSource.instances.push(this);
	}

	addEventListener(type: string, cb: (e: MessageEvent) => void) {
		(this.listeners[type] ??= []).push(cb);
	}

	close() {
		this.closed = true;
	}

	emit(type: string, data: unknown) {
		const ev = { data: JSON.stringify(data) } as MessageEvent;
		for (const cb of this.listeners[type] ?? []) cb(ev);
	}
}

beforeEach(() => {
	FakeEventSource.instances = [];
	// Streams take from a shared connection budget; without a reset the earlier
	// tests' unstopped streams would starve the later ones.
	resetStreamSlots();
	vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
});

afterEach(() => {
	vi.unstubAllGlobals();
});

import { MAX_CONCURRENT_STREAMS, resetStreamSlots } from './streamSlots';

const { createDownloadStream } = await import('./DownloadSSE.svelte');

describe('createDownloadStream', () => {
	it('maps progress events to rune state', () => {
		const s = createDownloadStream();
		s.start('t1');
		FakeEventSource.instances[0].emit('progress', {
			bytes_downloaded: 5,
			bytes_total: 10,
			files_completed: 1,
			files_total: 2,
			progress_percent: 50,
			candidate_index: 1,
			source: 'soulseek',
			quality_format: 'flac',
			quality_bit_depth: 16,
			quality_sample_rate: 44100,
			advertised_queue_depth: 0,
			queue_position_start: 91,
			queue_position_end: 100,
			remote_queued: true,
			preferred_quality_fallback_at: 1234.5,
			attempt_number: 1,
			attempt_total: 3,
			has_next_source: true
		});
		expect(s.state.progress?.progress_percent).toBe(50);
		expect(s.state.progress?.bytes_total).toBe(10);
		expect(s.state.source).toEqual({
			candidate_index: 1,
			source: 'soulseek',
			quality_format: 'flac',
			quality_bit_depth: 16,
			quality_sample_rate: 44100,
			advertised_queue_depth: 0,
			queue_position_start: 91,
			queue_position_end: 100,
			remote_queued: true,
			preferred_quality_fallback_at: 1234.5,
			attempt_number: 1,
			attempt_total: 3,
			has_next_source: true
		});
	});

	it('captures status events', () => {
		const s = createDownloadStream();
		s.start('t1');
		FakeEventSource.instances[0].emit('progress', {
			queue_position_start: 91,
			queue_position_end: 100
		});
		FakeEventSource.instances[0].emit('status', {
			status: 'retrying',
			candidate_index: 1,
			source: 'soulseek',
			quality_format: 'flac',
			quality_bit_depth: 16,
			quality_sample_rate: 44100,
			advertised_queue_depth: 0,
			queue_position_start: null,
			queue_position_end: null,
			remote_queued: false,
			attempt: 2,
			attempt_total: 3,
			has_next_source: true
		});
		expect(s.state.status).toBe('retrying');
		expect(s.state.source?.attempt_number).toBe(2);
		expect(s.state.source?.quality_bit_depth).toBe(16);
		expect(s.state.source?.candidate_index).toBe(1);
		expect(s.state.source?.queue_position_start).toBeNull();
		expect(s.state.source?.remote_queued).toBe(false);
	});

	it('marks done and closes the stream on the complete event', () => {
		const s = createDownloadStream();
		s.start('t1');
		const es = FakeEventSource.instances[0];
		es.emit('complete', { status: 'completed' });
		expect(s.state.done).toBe(true);
		expect(s.state.status).toBe('completed');
		expect(es.closed).toBe(true);
	});

	it('stop() closes the underlying EventSource', () => {
		const s = createDownloadStream();
		s.start('t1');
		const es = FakeEventSource.instances[0];
		s.stop();
		expect(es.closed).toBe(true);
	});
});

describe('connection budget', () => {
	it('opens no EventSource once the budget is spent', () => {
		const streams = Array.from({ length: MAX_CONCURRENT_STREAMS }, (_value, index) => {
			const s = createDownloadStream();
			s.start(`task-${index}`);
			return s;
		});
		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS);

		// One album can have more tracks transferring than the browser has
		// connections; the surplus falls back to polled progress instead of
		// starving every other request on the page.
		const refused = createDownloadStream();
		refused.start('one-too-many');

		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS);
		streams.forEach((s) => s.stop());
	});

	it('lets a waiting caller through once a stream stops', () => {
		const held = Array.from({ length: MAX_CONCURRENT_STREAMS }, (_value, index) => {
			const s = createDownloadStream();
			s.start(`task-${index}`);
			return s;
		});

		held[0].stop();
		const next = createDownloadStream();
		next.start('later');

		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS + 1);
		next.stop();
		held.slice(1).forEach((s) => s.stop());
	});

	it('does not reopen a healthy stream for the same task', () => {
		const s = createDownloadStream();
		s.start('t1');
		s.start('t1');

		expect(FakeEventSource.instances).toHaveLength(1);
		expect(FakeEventSource.instances[0].closed).toBe(false);
		s.stop();
	});

	it('upgrades a refused caller to a live stream when a slot frees', () => {
		const held = Array.from({ length: MAX_CONCURRENT_STREAMS }, (_value, index) => {
			const s = createDownloadStream();
			s.start(`task-${index}`);
			return s;
		});
		const refused = createDownloadStream();
		refused.start('waiting');
		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS);

		// No second start() call: the waiter queue promotes it, so the caller
		// never has to re-run and tear down its own state to get a stream.
		held[0].stop();

		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS + 1);
		refused.stop();
		held.slice(1).forEach((s) => s.stop());
	});

	it('stops waiting for a slot once the caller gives up', () => {
		const held = Array.from({ length: MAX_CONCURRENT_STREAMS }, (_value, index) => {
			const s = createDownloadStream();
			s.start(`task-${index}`);
			return s;
		});
		const abandoned = createDownloadStream();
		abandoned.start('gone');
		abandoned.stop();

		held[0].stop();

		// The abandoned caller must not be handed the freed slot.
		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS);
		held.slice(1).forEach((s) => s.stop());
	});

	it('releases its slot when the transfer completes', () => {
		const first = createDownloadStream();
		first.start('t1');
		FakeEventSource.instances[0].emit('complete', { status: 'completed' });

		const others = Array.from({ length: MAX_CONCURRENT_STREAMS }, (_value, index) => {
			const s = createDownloadStream();
			s.start(`after-${index}`);
			return s;
		});

		expect(FakeEventSource.instances).toHaveLength(MAX_CONCURRENT_STREAMS + 1);
		others.forEach((s) => s.stop());
	});
});
