import { createMutation, createQuery, queryOptions } from '@tanstack/svelte-query';

import { api } from '$lib/api/client';
import { API } from '$lib/constants';
import { invalidateQueriesWithPersister } from '$lib/queries/QueryClient';
import type { ImportReviewAcceptResponse, ImportReviewPage } from '$lib/types';

const KEY = ['import-review'] as const;

const listOptions = () =>
	queryOptions({
		// No stale window: a review disappears the moment somebody answers it, and
		// a stale list would offer to answer it again.
		staleTime: 0,
		queryKey: [...KEY, 'pending'] as const,
		queryFn: ({ signal }) =>
			api.global.get<ImportReviewPage>(API.importReview.list(), { signal })
	});

// enabled-getter so the endpoint (admin-only, 403 for plain users) is not fired
// from a page a regular user is looking at.
export const getImportReviewsQuery = (getEnabled: () => boolean = () => true) =>
	createQuery(() => ({ ...listOptions(), enabled: getEnabled() }));

export function acceptImportReview() {
	return createMutation(() => ({
		mutationFn: (id: string) =>
			api.global.post<ImportReviewAcceptResponse>(API.importReview.accept(id), {}),
		onSuccess: () => invalidateQueriesWithPersister({ queryKey: KEY })
	}));
}

export function dismissImportReview() {
	return createMutation(() => ({
		mutationFn: (id: string) =>
			api.global.post<ImportReviewAcceptResponse>(API.importReview.dismiss(id), {}),
		onSuccess: () => invalidateQueriesWithPersister({ queryKey: KEY })
	}));
}
