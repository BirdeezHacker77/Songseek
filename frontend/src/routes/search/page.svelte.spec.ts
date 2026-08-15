import { page } from '@vitest/browser/context';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import { searchStore } from '$lib/stores/search';
import SearchPage from './+page.svelte';

const originalFetch = globalThis.fetch;

function jsonResponse(body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});
}

describe('search result enrichment demand', () => {
	beforeEach(() => searchStore.clear());
	afterEach(() => {
		globalThis.fetch = originalFetch;
	});

	it('renders usable primary results without enrichment traffic, then enriches on intent', async () => {
		const mockFetch = vi.fn(async (input: RequestInfo | URL) => {
			const url = String(input);
			if (url.startsWith('/api/v1/search?q=')) {
				return jsonResponse({
					artists: [
						{
							type: 'artist',
							title: 'Muse',
							musicbrainz_id: 'artist-1',
							in_library: false,
							score: 95
						}
					],
					albums: [],
					top_artist: null,
					top_album: null
				});
			}
			if (url === '/api/v1/search/enrich/batch') {
				return jsonResponse({
					artists: [{ musicbrainz_id: 'artist-1', listen_count: 100 }],
					albums: [],
					source: 'listenbrainz'
				});
			}
			throw new Error(`Unexpected request: ${url}`);
		});
		globalThis.fetch = mockFetch as typeof fetch;

		render(SearchPage, { data: { query: 'muse' } });
		await expect.element(page.getByText('Muse')).toBeInTheDocument();
		await new Promise((resolve) => setTimeout(resolve, 250));

		const enrichmentCalls = () =>
			mockFetch.mock.calls.filter(([input]) => String(input) === '/api/v1/search/enrich/batch');
		expect(enrichmentCalls()).toHaveLength(0);
		expect(mockFetch).toHaveBeenCalledTimes(1);

		await page.getByText('Muse').hover();
		await vi.waitFor(() => expect(enrichmentCalls()).toHaveLength(1));
	});
});
