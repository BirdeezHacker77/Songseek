import { beforeEach, describe, expect, it } from 'vitest';

import {
	MAX_CONCURRENT_STREAMS,
	acquireStreamSlot,
	releaseStreamSlot,
	resetStreamSlots,
	streamSlotsInUse
} from './streamSlots';

describe('EventSource connection budget', () => {
	beforeEach(() => resetStreamSlots());

	it('hands out only as many slots as the cap allows', () => {
		const granted = Array.from({ length: MAX_CONCURRENT_STREAMS + 3 }, () => acquireStreamSlot());

		expect(granted.filter(Boolean)).toHaveLength(MAX_CONCURRENT_STREAMS);
		expect(streamSlotsInUse()).toBe(MAX_CONCURRENT_STREAMS);
	});

	it('stays well under the six connections HTTP/1.1 allows per origin', () => {
		// The rest are needed by the library-activity stream, queries and images.
		// If this ever reaches 6, an album mid-download starves the whole page.
		expect(MAX_CONCURRENT_STREAMS).toBeLessThan(6);
	});

	it('frees a slot for the next caller when a stream ends', () => {
		while (acquireStreamSlot());
		expect(acquireStreamSlot()).toBe(false);

		releaseStreamSlot();

		expect(acquireStreamSlot()).toBe(true);
		expect(streamSlotsInUse()).toBe(MAX_CONCURRENT_STREAMS);
	});

	it('ignores a release from a caller that never held a slot', () => {
		releaseStreamSlot();

		expect(streamSlotsInUse()).toBe(0);
		// Would otherwise let the count go negative and hand out unlimited slots.
		const granted = Array.from({ length: MAX_CONCURRENT_STREAMS + 2 }, () => acquireStreamSlot());
		expect(granted.filter(Boolean)).toHaveLength(MAX_CONCURRENT_STREAMS);
	});
});
