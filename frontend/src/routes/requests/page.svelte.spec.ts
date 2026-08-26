import { page } from '@vitest/browser/context';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';

const h = vi.hoisted(() => {
	// Helpers live inside the hoisted block because vi.mock factories are lifted
	// above every top-level binding in the module.
	const emptyQuery = (data: unknown) => () => ({
		get data() {
			return data;
		},
		isLoading: false,
		isError: false
	});
	const noopMutation = () => ({
		mutate: () => {},
		mutateAsync: async () => ({}),
		isPending: false
	});
	const state = {
		loaded: true,
		configured: false,
		isAdmin: false,
		isTrusted: false,
		tab: null as string | null,
		emptyQuery,
		noopMutation,
		appPage: {
			get url() {
				return new URL(
					state.tab ? `http://localhost/requests?tab=${state.tab}` : 'http://localhost/requests'
				);
			}
		}
	};
	return state;
});

vi.mock('$app/state', () => ({ page: h.appPage }));

vi.mock('$lib/queries/HomeIntegrationStatusQuery.svelte', () => ({
	getIntegrationStatusQuery: () => ({
		get isLoading() {
			return !h.loaded;
		},
		get data() {
			return h.loaded ? { download_client: h.configured } : undefined;
		}
	})
}));

vi.mock('$lib/stores/authStore.svelte', () => ({
	authStore: {
		get isAdmin() {
			return h.isAdmin;
		},
		get isTrusted() {
			return h.isTrusted;
		},
		get user() {
			return { id: 'user-1' };
		}
	}
}));

vi.mock('$lib/utils/requestsApi', () => ({
	fetchActiveRequests: vi.fn().mockResolvedValue({ items: [], count: 0 }),
	fetchRequestHistory: vi.fn().mockResolvedValue({ items: [], total: 0 }),
	fetchPendingApprovals: vi.fn().mockResolvedValue({ items: [], count: 0 }),
	cancelRequest: vi.fn(),
	retryRequest: vi.fn(),
	clearHistoryItem: vi.fn(),
	approveRequest: vi.fn(),
	rejectRequest: vi.fn(),
	notifyPendingApprovalCountChanged: vi.fn()
}));

vi.mock('$lib/queries/wanted/WantedQuery.svelte', () => ({
	getWantedWatchesQuery: h.emptyQuery({ items: [], retrying: [] })
}));
vi.mock('$lib/queries/wanted/WantedMutations.svelte', () => ({
	createStopWatchMutation: h.noopMutation,
	createResumeWatchMutation: h.noopMutation,
	createMarkWantedSeenMutation: h.noopMutation
}));

vi.mock('$lib/queries/following/AdminApprovalsQueries.svelte', () => ({
	getAutoDownloadApprovalsQuery: h.emptyQuery({ items: [], count: 0 }),
	getAutoDownloadApprovalBatchesQuery: h.emptyQuery({ batches: [], count: 0 })
}));
vi.mock('$lib/queries/following/AdminApprovalsMutations.svelte', () => ({
	createApproveAutoDownloadMutation: h.noopMutation,
	createRejectAutoDownloadMutation: h.noopMutation,
	createApproveAutoDownloadBatchMutation: h.noopMutation,
	createRejectAutoDownloadBatchMutation: h.noopMutation
}));

vi.mock('$lib/queries/scrobble-preferences/PersonalMixApprovalsQuery.svelte', () => ({
	getPersonalMixApprovalsQuery: h.emptyQuery({ items: [], count: 0 })
}));
vi.mock('$lib/queries/scrobble-preferences/ScrobblePreferencesMutations.svelte', () => ({
	createApprovePersonalMixMutation: h.noopMutation,
	createRejectPersonalMixMutation: h.noopMutation
}));

vi.mock('$lib/queries/downloads/UpgradeQueries.svelte', () => ({
	getCutoffUnmetQuery: h.emptyQuery({ items: [] }),
	requestUpgradeAlbum: h.noopMutation
}));

vi.mock('$lib/queries/import/DropImportQueries.svelte', () => ({
	getDropImportJobsQuery: () => ({ data: { jobs: [] }, isLoading: false })
}));
vi.mock('$lib/queries/import/DropImportMutations.svelte', () => ({
	uploadDropMutation: h.noopMutation,
	matchDropItemMutation: h.noopMutation,
	discardDropItemMutation: h.noopMutation
}));

import RequestsPage from './+page.svelte';

describe('/requests page', () => {
	beforeEach(() => {
		h.loaded = true;
		h.configured = false;
		h.isAdmin = false;
		h.isTrusted = false;
		h.tab = null;
	});

	it('collapses the old eight-tab split into four', async () => {
		h.isAdmin = true;
		h.isTrusted = true;
		render(RequestsPage);

		for (const name of ['Activity', 'Wanted', 'Approvals', 'Automation']) {
			await expect.element(page.getByRole('tab', { name, exact: false })).toBeVisible();
		}
		// The surfaces these replaced must not survive as tabs of their own.
		for (const gone of ['Active', 'History', 'Upgrades', 'Import']) {
			await expect
				.element(page.getByRole('tab', { name: gone, exact: true }))
				.not.toBeInTheDocument();
		}
	});

	it("keeps Approvals and Automation out of a plain user's tab list", async () => {
		render(RequestsPage);

		await expect.element(page.getByRole('tab', { name: 'Activity' })).toBeVisible();
		await expect.element(page.getByRole('tab', { name: 'Approvals' })).not.toBeInTheDocument();
		await expect.element(page.getByRole('tab', { name: 'Automation' })).not.toBeInTheDocument();
	});

	it('shows the admin setup CTA when no download client is configured', async () => {
		h.isAdmin = true;
		h.isTrusted = true;
		render(RequestsPage);

		await expect.element(page.getByText('Download client not configured')).toBeVisible();
		await expect
			.element(page.getByRole('link', { name: 'Configure Download Client' }))
			.toBeVisible();
	});

	it('shows a non-admin message without the CTA', async () => {
		render(RequestsPage);

		await expect
			.element(page.getByText('Contact your admin to configure the download client.'))
			.toBeVisible();
		await expect
			.element(page.getByRole('link', { name: 'Configure Download Client' }))
			.not.toBeInTheDocument();
	});

	it('shows a loading skeleton before integration status resolves', async () => {
		h.loaded = false;
		const { container } = render(RequestsPage);

		expect(container.querySelector('.skeleton')).not.toBeNull();
	});

	it('hides the Imports section from plain users', async () => {
		render(RequestsPage);

		await expect.element(page.getByText('Download client not configured')).toBeVisible();
		await expect.element(page.getByText('Imports')).not.toBeInTheDocument();
	});

	it('lets a curator open Imports and reach the drop zone', async () => {
		h.isTrusted = true;
		render(RequestsPage);

		await page.getByText('Imports').click();
		await expect.element(page.getByText('Drop your purchases here')).toBeVisible();
	});

	it('shows the everyone toggle only to admins inside Imports', async () => {
		h.isTrusted = true;
		h.isAdmin = true;
		render(RequestsPage);

		await page.getByText('Imports').click();
		await expect.element(page.getByText("Show everyone's imports")).toBeVisible();
	});

	it('opens the Imports section for the old ?tab=import deep link', async () => {
		h.isTrusted = true;
		h.tab = 'import';
		render(RequestsPage);

		await expect.element(page.getByText('Drop your purchases here')).toBeVisible();
	});

	it('maps the retired ?tab=upgrades link onto Automation', async () => {
		h.isAdmin = true;
		h.isTrusted = true;
		h.tab = 'upgrades';
		render(RequestsPage);

		await expect
			.element(page.getByRole('tab', { name: 'Automation' }))
			.toHaveAttribute('aria-selected', 'true');
	});
});
