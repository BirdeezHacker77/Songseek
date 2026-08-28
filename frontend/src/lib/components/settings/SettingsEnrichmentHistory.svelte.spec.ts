import { page } from '@vitest/browser/context';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';

import type { EnrichmentHistoryItem } from '$lib/types';

const h = vi.hoisted(() => ({
	isAdmin: true,
	restored: [] as string[],
	items: [] as EnrichmentHistoryItem[]
}));

vi.mock('$lib/stores/authStore.svelte', () => ({
	authStore: {
		get isAdmin() {
			return h.isAdmin;
		}
	}
}));

vi.mock('$lib/stores/toast', () => ({ toastStore: { show: () => {} } }));

vi.mock('$lib/queries/downloads/EnrichmentHistoryQueries.svelte', () => ({
	getEnrichmentHistoryQuery: () => ({
		get data() {
			return { items: h.items };
		},
		isLoading: false,
		isError: false
	}),
	restoreEnrichmentChange: () => ({
		mutateAsync: async (id: string) => {
			h.restored.push(id);
			return { id };
		},
		isPending: false
	})
}));

import SettingsEnrichmentHistory from './SettingsEnrichmentHistory.svelte';

function entry(overrides: Partial<EnrichmentHistoryItem> = {}): EnrichmentHistoryItem {
	return {
		id: 'one',
		file_path: '/music/Foo Fighters/The Colour and the Shape/01 Doll.flac',
		kinds: ['tags', 'lyrics'],
		changed_fields: ['album', 'title', 'lyrics_plain'],
		created_at: 1_700_000_000,
		restored_at: null,
		...overrides
	};
}

describe('Settings > Enrichment history', () => {
	beforeEach(() => {
		h.isAdmin = true;
		h.restored = [];
		h.items = [];
	});

	it('says nothing has changed yet rather than showing an empty table', async () => {
		render(SettingsEnrichmentHistory);

		await expect.element(page.getByText('Nothing changed yet')).toBeVisible();
	});

	it('names the file and what was changed to it', async () => {
		h.items = [entry()];
		render(SettingsEnrichmentHistory);

		await expect.element(page.getByText('01 Doll.flac')).toBeVisible();
		// The stored kinds are field-level names; these are what a person reads.
		await expect.element(page.getByText('Tags')).toBeVisible();
		await expect.element(page.getByText('Lyrics')).toBeVisible();
	});

	it('restores a change when asked', async () => {
		h.items = [entry()];
		render(SettingsEnrichmentHistory);

		await page.getByRole('button', { name: 'Restore' }).click();

		expect(h.restored).toEqual(['one']);
	});

	it('offers no second restore for a change already put back', async () => {
		h.items = [entry({ restored_at: 1_700_000_500 })];
		render(SettingsEnrichmentHistory);

		await expect.element(page.getByText('Restored', { exact: false })).toBeVisible();
		expect(page.getByRole('button', { name: 'Restore' }).elements()).toHaveLength(0);
	});

	it('tells a non-admin they cannot see this', async () => {
		h.isAdmin = false;
		render(SettingsEnrichmentHistory);

		await expect
			.element(page.getByText('Only an administrator can see enrichment history.'))
			.toBeVisible();
	});
});
