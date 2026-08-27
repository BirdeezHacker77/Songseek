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
			replaygain: { enabled: false, album_aware: true }
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
			replaygain: { enabled: false, album_aware: true }
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

		const fieldset = container.querySelector('fieldset');
		expect(fieldset?.hasAttribute('disabled')).toBe(true);

		await page.getByText('Grab lyrics on future downloads').click();

		expect(container.querySelector('fieldset')?.hasAttribute('disabled')).toBe(false);
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
});
