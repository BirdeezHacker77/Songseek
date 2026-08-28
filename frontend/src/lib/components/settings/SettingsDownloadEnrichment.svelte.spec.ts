import { page } from '@vitest/browser/context';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';

const h = vi.hoisted(() => {
	const state = {
		isAdmin: true,
		saved: [] as unknown[],
		data: {
			lyrics: {
				enabled: false,
				provider: 'lrclib' as const,
				embed_in_tags: true,
				prefer_synced: true,
				write_lrc_file: true
			},
			replaygain: { enabled: false, album_aware: true },
			refresh: { enabled: false, navidrome_enabled: false, jellyfin_enabled: false },
			genres: {
				enabled: false,
				canonicalize: true,
				known_genres_only: true,
				maximum_count: 5,
				denylist: []
			},
			tagging: {
				enabled: false,
				auto_accept_score: 0.7,
				review_score: 0.5,
				write_identifiers: true,
				rewrite_titles: true
			},
			artwork: {
				enabled: false,
				embed_in_tags: true,
				save_cover_file: true,
				minimum_width: 500
			}
		}
	};
	return state;
});

vi.mock('$lib/stores/authStore.svelte', () => ({
	authStore: {
		get isAdmin() {
			return h.isAdmin;
		}
	}
}));

vi.mock('$lib/stores/toast', () => ({ toastStore: { show: () => {} } }));

vi.mock('$lib/queries/downloads/DownloadClientsQueries.svelte', () => ({
	getDownloadEnrichmentQuery: () => ({
		get data() {
			return h.data;
		},
		isLoading: false,
		isError: false
	}),
	saveDownloadEnrichment: () => ({
		mutateAsync: async (settings: unknown) => {
			h.saved.push(settings);
			return settings;
		},
		isPending: false
	})
}));

import SettingsDownloadEnrichment from './SettingsDownloadEnrichment.svelte';

describe('Settings > Enrichment', () => {
	beforeEach(() => {
		h.isAdmin = true;
		h.saved = [];
		h.data = {
			lyrics: {
				enabled: false,
				provider: 'lrclib',
				embed_in_tags: true,
				prefer_synced: true,
				write_lrc_file: true
			},
			replaygain: { enabled: false, album_aware: true },
			refresh: { enabled: false, navidrome_enabled: false, jellyfin_enabled: false },
			genres: {
				enabled: false,
				canonicalize: true,
				known_genres_only: true,
				maximum_count: 5,
				denylist: []
			},
			tagging: {
				enabled: false,
				auto_accept_score: 0.7,
				review_score: 0.5,
				write_identifiers: true,
				rewrite_titles: true
			},
			artwork: {
				enabled: false,
				embed_in_tags: true,
				save_cover_file: true,
				minimum_width: 500
			}
		};
	});

	it('offers the two switches without mentioning profiles or dry runs', async () => {
		render(SettingsDownloadEnrichment);

		await expect.element(page.getByText('Grab lyrics on future downloads')).toBeVisible();
		await expect.element(page.getByText('Calculate ReplayGain on future downloads')).toBeVisible();
		await expect.element(page.getByText('Save a .lrc file next to the song')).toBeVisible();
	});

	it('keeps the lyrics options inert until lyrics are switched on', async () => {
		const { container } = render(SettingsDownloadEnrichment);
		// By card order, not the first fieldset on the page - identification sits
		// above lyrics, and querying blind would assert against the wrong one.
		const lyricsFieldset = () => container.querySelectorAll('fieldset')[1];

		expect(lyricsFieldset()?.hasAttribute('disabled')).toBe(true);

		await page.getByText('Grab lyrics on future downloads').click();

		expect(lyricsFieldset()?.hasAttribute('disabled')).toBe(false);
	});

	it('saves only after something changes, and sends the edited settings', async () => {
		render(SettingsDownloadEnrichment);
		const save = page.getByRole('button', { name: 'Save' });
		await expect.element(save).toBeDisabled();

		await page.getByText('Grab lyrics on future downloads').click();
		await save.click();

		expect(h.saved).toHaveLength(1);
		expect((h.saved[0] as typeof h.data).lyrics.enabled).toBe(true);
	});

	it('tells a non-admin they cannot change these', async () => {
		h.isAdmin = false;
		render(SettingsDownloadEnrichment);

		await expect
			.element(page.getByText('Only an administrator can change enrichment settings.'))
			.toBeVisible();
	});

	it('offers a media server refresh that does not need Library Management', async () => {
		render(SettingsDownloadEnrichment);

		await expect.element(page.getByText('Refresh after a download is imported')).toBeVisible();
		await expect.element(page.getByText('Navidrome')).toBeVisible();
	});

	it('offers genre tidying as a plain toggle', async () => {
		render(SettingsDownloadEnrichment);

		await expect.element(page.getByText('Tidy genres on future downloads')).toBeVisible();
		await expect.element(page.getByText('Drop anything unrecognised')).toBeVisible();
	});

	it('offers MusicBrainz retagging as a plain toggle', async () => {
		render(SettingsDownloadEnrichment);

		await expect
			.element(page.getByText('Rewrite tags from MusicBrainz on future downloads'))
			.toBeVisible();
		await expect.element(page.getByText('Correct titles and track numbers')).toBeVisible();
	});

	it('shows the thresholds as percentages and saves them as fractions', async () => {
		render(SettingsDownloadEnrichment);
		await page.getByText('Rewrite tags from MusicBrainz on future downloads').click();

		const auto = page.getByRole('spinbutton').nth(0);
		await expect.element(auto).toHaveValue(70);

		await auto.fill('85');
		await page.getByRole('button', { name: 'Save' }).click();

		expect((h.saved[0] as typeof h.data).tagging.auto_accept_score).toBe(0.85);
	});

	it('warns when asking about matches better than it accepts automatically', async () => {
		h.data.tagging = { ...h.data.tagging, enabled: true, review_score: 0.9 };
		render(SettingsDownloadEnrichment);

		await expect
			.element(page.getByText('Asking above the automatic threshold', { exact: false }))
			.toBeVisible();
	});

	it('offers cover art as a fill-only toggle', async () => {
		render(SettingsDownloadEnrichment);

		await expect.element(page.getByText('Fetch missing cover art on future downloads')).toBeVisible();
		await expect
			.element(page.getByText('It never replaces artwork a download already has.', { exact: false }))
			.toBeVisible();
	});
});
