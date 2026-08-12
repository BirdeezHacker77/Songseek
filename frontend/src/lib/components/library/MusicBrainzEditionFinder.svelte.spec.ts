import { page } from '@vitest/browser/context';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';

const releaseMbid = '428b6417-8a4d-4a5b-b1a3-8762002167a8';
const h = vi.hoisted(() => ({
	getQuery: (() => '') as () => string,
	getOffset: (() => 0) as () => number,
	refetch: vi.fn(),
	queryState: {
		data: {
			query: 'Signal Artist Local Signals',
			items: [
				{
					release_mbid: '428b6417-8a4d-4a5b-b1a3-8762002167a8',
					release_group_mbid: 'group-1',
					artist_name: 'Signal Artist',
					title: 'Local Signals',
					date: '2024-02-03',
					country: 'GB',
					status: 'Official',
					packaging: 'Digipak',
					media_formats: ['CD'],
					disc_count: 1,
					track_count: 12,
					label: 'Signal Records',
					catalogue_number: 'SIG-12',
					barcode: '123456',
					disambiguation: 'deluxe booklet',
					musicbrainz_url: 'https://musicbrainz.org/release/428b6417-8a4d-4a5b-b1a3-8762002167a8',
					score: 99,
					belongs_to_current_release_group: true
				}
			],
			total: 20,
			offset: 0,
			limit: 12
		},
		isLoading: false,
		isFetching: false,
		isError: false
	}
}));

vi.mock('$lib/stores/authStore.svelte', () => ({
	authStore: { user: { id: 'admin-1' } }
}));
vi.mock('$lib/queries/library/LibraryEditionQueries.svelte', () => ({
	getReleaseEditionSearchQuery: (
		_getUserId: () => string,
		_getAlbumId: () => string,
		getQuery: () => string,
		getOffset: () => number
	) => {
		h.getQuery = getQuery;
		h.getOffset = getOffset;
		return { ...h.queryState, refetch: h.refetch };
	}
}));

import MusicBrainzEditionFinder from './MusicBrainzEditionFinder.svelte';

beforeEach(() => {
	vi.clearAllMocks();
	h.queryState.isLoading = false;
	h.queryState.isFetching = false;
	h.queryState.isError = false;
	h.queryState.data.items = [
		{
			...h.queryState.data.items[0],
			release_mbid: releaseMbid
		}
	];
	h.queryState.data.total = 20;
	h.queryState.data.offset = 0;
	h.refetch.mockResolvedValue({});
});

describe('MusicBrainzEditionFinder', () => {
	it('prefills editable search, pages, and checks only the selected exact release', async () => {
		const oncheck = vi.fn();
		render(MusicBrainzEditionFinder, {
			props: {
				albumId: 'album-1',
				artistName: 'Signal Artist',
				albumTitle: 'Local Signals',
				oncheck
			}
		} as unknown as Parameters<typeof render>[1]);

		const search = page.getByRole('textbox', { name: 'Search MusicBrainz releases' });
		await expect.element(search).toHaveValue('Signal Artist Local Signals');
		await search.fill('Signal Artist catalogue SIG-12');
		await page.getByRole('button', { name: 'Search', exact: true }).click();
		expect(h.getQuery()).toBe('Signal Artist catalogue SIG-12');
		await expect.element(page.getByText('Current release group')).toBeVisible();
		await expect.element(page.getByText(/CD · Digipak · 1 disc · 12 tracks/)).toBeVisible();
		await page.getByRole('button', { name: /Check this edition/ }).click();
		expect(oncheck).toHaveBeenCalledOnce();
		expect(oncheck).toHaveBeenCalledWith(releaseMbid);
		await page.getByRole('button', { name: /Next/ }).click();
		expect(h.getOffset()).toBe(12);
		await expect
			.element(page.getByRole('link', { name: /Open this search on MusicBrainz/ }))
			.toHaveAttribute('target', '_blank');
	});

	it('accepts a canonical MusicBrainz release URL', async () => {
		const oncheck = vi.fn();
		render(MusicBrainzEditionFinder, {
			props: {
				albumId: 'album-1',
				artistName: 'Signal Artist',
				albumTitle: 'Local Signals',
				oncheck
			}
		} as unknown as Parameters<typeof render>[1]);

		await page.getByText(/Already know the release/).click();
		await page
			.getByRole('textbox', { name: 'MusicBrainz release UUID or URL' })
			.fill(`https://musicbrainz.org/release/${releaseMbid}`);
		await page.getByRole('button', { name: 'Check exact release' }).click();
		expect(oncheck).toHaveBeenCalledWith(releaseMbid);
	});

	it('shows empty and provider-unavailable states independently', async () => {
		h.queryState.data.items = [];
		h.queryState.data.total = 0;
		const empty = render(MusicBrainzEditionFinder, {
			props: {
				albumId: 'album-1',
				artistName: 'Signal Artist',
				albumTitle: 'Local Signals',
				oncheck: vi.fn()
			}
		} as unknown as Parameters<typeof render>[1]);
		await expect.element(page.getByText('No editions found')).toBeVisible();
		empty.unmount();

		h.queryState.isError = true;
		render(MusicBrainzEditionFinder, {
			props: {
				albumId: 'album-1',
				artistName: 'Signal Artist',
				albumTitle: 'Local Signals',
				oncheck: vi.fn()
			}
		} as unknown as Parameters<typeof render>[1]);
		await expect.element(page.getByText('MusicBrainz is unavailable')).toBeVisible();
		await page.getByRole('button', { name: 'Retry' }).click();
		expect(h.refetch).toHaveBeenCalledOnce();
	});
});
