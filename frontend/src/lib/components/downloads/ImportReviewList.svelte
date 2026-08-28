<script lang="ts">
	import { Check, ScanSearch, X } from 'lucide-svelte';
	import { fly } from 'svelte/transition';

	import {
		acceptImportReview,
		dismissImportReview,
		getImportReviewsQuery
	} from '$lib/queries/downloads/ImportReviewQueries.svelte';
	import { toastStore } from '$lib/stores/toast';
	import type { ImportReviewEntry } from '$lib/types';

	let { enabled = true }: { enabled?: boolean } = $props();

	const query = getImportReviewsQuery(() => enabled);
	const accept = acceptImportReview();
	const dismiss = dismissImportReview();

	let busy = $state<string | null>(null);

	const items = $derived(query.data?.items ?? []);

	async function handleAccept(entry: ImportReviewEntry): Promise<void> {
		busy = entry.id;
		try {
			const result = await accept.mutateAsync(entry.id);
			toastStore.show({
				message:
					result.files_written > 0
						? `Retagged ${result.files_written} ${result.files_written === 1 ? 'track' : 'tracks'} as ${entry.album_title}`
						: // Zero is a normal outcome: the files already carried those tags.
							`${entry.album_title} already matched - nothing to change`,
				type: 'success'
			});
		} catch (error) {
			toastStore.show({
				message: error instanceof Error ? error.message : 'Could not apply that match',
				type: 'error'
			});
		} finally {
			busy = null;
		}
	}

	async function handleDismiss(entry: ImportReviewEntry): Promise<void> {
		busy = entry.id;
		try {
			await dismiss.mutateAsync(entry.id);
			toastStore.show({ message: 'Left as it is', type: 'success' });
		} catch (error) {
			toastStore.show({
				message: error instanceof Error ? error.message : 'Could not dismiss that review',
				type: 'error'
			});
		} finally {
			busy = null;
		}
	}
</script>

{#if items.length > 0}
	<section class="mb-6">
		<div class="mb-2.5 flex items-center gap-2">
			<ScanSearch class="text-base-content/40 h-4 w-4" aria-hidden="true" />
			<h2 class="text-sm font-semibold">Identification reviews</h2>
			<span class="badge badge-sm badge-ghost">{query.data?.total ?? items.length}</span>
		</div>
		<p class="text-base-content/40 mb-3 text-xs">
			These downloads look like a release on MusicBrainz, but not closely enough to retag on their
			own. They are already in your library and play fine either way.
		</p>

		<div class="flex flex-col gap-2.5">
			{#each items as entry, index (entry.id)}
				<div
					in:fly={{ y: 12, duration: 200, delay: index * 30 }}
					class="bg-base-200 rounded-box flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:gap-4 sm:p-4"
				>
					<div class="min-w-0 flex-1">
						<div class="mb-1 flex items-center gap-2">
							<span class="badge badge-sm badge-warning badge-outline"
								>{Math.round(entry.score * 100)}% match</span
							>
							<span class="text-base-content/40 text-xs"
								>{entry.paths.length}
								{entry.paths.length === 1 ? 'track' : 'tracks'}</span
							>
						</div>
						<p class="truncate text-sm font-semibold">
							{entry.album_title}
							{#if entry.album_artist_name}
								<span class="text-base-content/60 font-normal">— {entry.album_artist_name}</span>
							{/if}
						</p>
						<p class="text-base-content/40 truncate text-xs">
							Your copy says:
							<span class="text-base-content/60">{entry.local_album_title || 'untitled'}</span>
							{#if entry.local_album_artist_name}
								<span class="text-base-content/60">— {entry.local_album_artist_name}</span>
							{/if}
						</p>
					</div>
					<div class="flex shrink-0 gap-2">
						<button
							class="btn btn-success btn-sm gap-1"
							disabled={busy !== null}
							onclick={() => void handleAccept(entry)}
						>
							{#if busy === entry.id && accept.isPending}
								<span class="loading loading-spinner loading-xs"></span>
							{:else}
								<Check class="h-3.5 w-3.5" />
							{/if}
							Use this
						</button>
						<button
							class="btn btn-sm btn-outline gap-1"
							disabled={busy !== null}
							onclick={() => void handleDismiss(entry)}
						>
							<X class="h-3.5 w-3.5" />
							Keep mine
						</button>
					</div>
				</div>
			{/each}
		</div>
	</section>
{/if}
