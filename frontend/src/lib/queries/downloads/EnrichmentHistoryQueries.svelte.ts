import { createMutation, createQuery, queryOptions } from '@tanstack/svelte-query';

import { api } from '$lib/api/client';
import { API } from '$lib/constants';
import { invalidateQueriesWithPersister } from '$lib/queries/QueryClient';
import type { EnrichmentHistoryItem, EnrichmentHistoryPage } from '$lib/types';

const KEY = ['enrichment-history'] as const;

const listOptions = () =>
	queryOptions({
		// No stale window: an entry can only be restored once, and a stale list
		// would offer to restore one that already has been.
		staleTime: 0,
		queryKey: [...KEY, 'recent'] as const,
		queryFn: ({ signal }) =>
			api.global.get<EnrichmentHistoryPage>(API.enrichmentHistory.list(), { signal })
	});

// enabled-getter so the endpoint (admin-only, 403 for plain users) is not fired
// from a page a regular user is looking at.
export const getEnrichmentHistoryQuery = (getEnabled: () => boolean = () => true) =>
	createQuery(() => ({ ...listOptions(), enabled: getEnabled() }));

export function restoreEnrichmentChange() {
	return createMutation(() => ({
		mutationFn: (id: string) =>
			api.global.post<EnrichmentHistoryItem>(API.enrichmentHistory.restore(id), {}),
		onSuccess: () => invalidateQueriesWithPersister({ queryKey: KEY })
	}));
}
