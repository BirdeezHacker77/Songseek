import { API } from '$lib/constants';
import { invalidateQueriesWithPersister } from '$lib/queries/QueryClient';
import { LibraryQueryKeyFactory } from './LibraryQueryKeyFactory';
import { invalidateLibraryCatalog } from './LibraryCatalogInvalidation';

export function createLibraryActivityEvents() {
	let activitySource: EventSource | null = null;
	let operationsSource: EventSource | null = null;
	let catalogRevision: number | null = null;

	function invalidateActivity(): void {
		void invalidateQueriesWithPersister({ queryKey: LibraryQueryKeyFactory.activityPrefix() });
	}

	function invalidateOperations(): void {
		void invalidateQueriesWithPersister({ queryKey: LibraryQueryKeyFactory.operationsPrefix() });
		void invalidateQueriesWithPersister({ queryKey: LibraryQueryKeyFactory.reviewsPrefix() });
		invalidateActivity();
	}

	function observeCatalogRevision(event: Event): void {
		if (!(event instanceof MessageEvent) || typeof event.data !== 'string') return;
		try {
			const payload = JSON.parse(event.data) as { revisions?: Record<string, unknown> };
			const nextRevision = payload.revisions?.catalog;
			if (typeof nextRevision !== 'number' || nextRevision === catalogRevision) return;
			catalogRevision = nextRevision;
			void invalidateLibraryCatalog();
		} catch {
			return;
		}
	}

	function activityChanged(event: Event): void {
		invalidateActivity();
		observeCatalogRevision(event);
	}

	function operationsChanged(event: Event): void {
		invalidateOperations();
		observeCatalogRevision(event);
	}

	function start(admin: boolean): void {
		stop();
		activitySource = new EventSource(API.library.activityStream());
		activitySource.addEventListener('open', invalidateActivity);
		activitySource.addEventListener('activity.changed', activityChanged);
		if (admin) {
			operationsSource = new EventSource(API.library.operationsStream());
			operationsSource.addEventListener('open', invalidateOperations);
			operationsSource.addEventListener('activity.changed', operationsChanged);
		}
	}

	function stop(): void {
		activitySource?.close();
		operationsSource?.close();
		activitySource = null;
		operationsSource = null;
		catalogRevision = null;
	}

	return { start, stop };
}
