<script lang="ts">
	import { History, Undo2 } from 'lucide-svelte';

	import {
		getEnrichmentHistoryQuery,
		restoreEnrichmentChange
	} from '$lib/queries/downloads/EnrichmentHistoryQueries.svelte';
	import { authStore } from '$lib/stores/authStore.svelte';
	import { toastStore } from '$lib/stores/toast';
	import type { EnrichmentHistoryItem } from '$lib/types';

	const query = getEnrichmentHistoryQuery(() => authStore.isAdmin);
	const restore = restoreEnrichmentChange();

	let busy = $state<string | null>(null);

	const items = $derived(query.data?.items ?? []);

	// The stored kinds are field-level names; these are what they mean to
	// somebody looking at their own library.
	const LABELS: Record<string, string> = {
		lyrics: 'Lyrics',
		replaygain: 'ReplayGain',
		genres: 'Genres',
		tags: 'Tags',
		artwork: 'Cover art'
	};

	function fileName(path: string): string {
		const parts = path.split(/[\\/]/);
		return parts[parts.length - 1] || path;
	}

	function folder(path: string): string {
		const parts = path.split(/[\\/]/);
		return parts.slice(-3, -1).join('/');
	}

	function when(seconds: number): string {
		return new Date(seconds * 1000).toLocaleString();
	}

	async function handleRestore(entry: EnrichmentHistoryItem): Promise<void> {
		busy = entry.id;
		try {
			await restore.mutateAsync(entry.id);
			toastStore.show({
				message: `Put ${fileName(entry.file_path)} back as it was`,
				type: 'success'
			});
		} catch (error) {
			toastStore.show({
				message: error instanceof Error ? error.message : 'Could not restore that change',
				type: 'error'
			});
		} finally {
			busy = null;
		}
	}
</script>

<div class="space-y-6">
	<div>
		<h2 class="text-xl font-bold">Enrichment history</h2>
		<p class="text-base-content/50 mt-0.5 text-sm">
			Every tag write enrichment made, and what the file said before it. Restoring one puts that
			file back exactly as it arrived.
		</p>
	</div>

	{#if !authStore.isAdmin}
		<div class="alert alert-info">Only an administrator can see enrichment history.</div>
	{:else if query.isLoading}
		<div class="space-y-2">
			{#each Array(4) as _, index (`history-loading-${index}`)}
				<div class="skeleton rounded-box h-16 w-full"></div>
			{/each}
		</div>
	{:else if query.isError}
		<div class="alert alert-error">Could not load enrichment history.</div>
	{:else if items.length === 0}
		<div class="flex flex-col items-center justify-center py-16 text-center">
			<div class="bg-base-200 mb-4 flex h-16 w-16 items-center justify-center rounded-full">
				<History class="text-base-content/25 h-8 w-8" />
			</div>
			<h3 class="text-base-content/50 mb-1.5 text-lg font-semibold">Nothing changed yet</h3>
			<p class="text-base-content/30 max-w-xs text-sm">
				Downloads enriched from now on will be listed here, newest first.
			</p>
		</div>
	{:else}
		<div class="flex flex-col gap-2">
			{#each items as entry (entry.id)}
				<div
					class="bg-base-200/55 rounded-box border-base-300 flex flex-col gap-3 border p-3 sm:flex-row sm:items-center sm:gap-4"
				>
					<div class="min-w-0 flex-1">
						<p class="truncate text-sm font-medium">{fileName(entry.file_path)}</p>
						<p class="text-base-content/40 truncate text-xs">{folder(entry.file_path)}</p>
						<div class="mt-1 flex flex-wrap items-center gap-1.5">
							{#each entry.kinds as kind (kind)}
								<span class="badge badge-sm badge-ghost">{LABELS[kind] ?? kind}</span>
							{/each}
							<span class="text-base-content/30 text-xs">{when(entry.created_at)}</span>
						</div>
					</div>
					<div class="shrink-0">
						{#if entry.restored_at}
							<span class="text-base-content/40 text-xs">Restored {when(entry.restored_at)}</span>
						{:else}
							<button
								class="btn btn-sm btn-outline gap-1"
								disabled={busy !== null}
								onclick={() => void handleRestore(entry)}
							>
								{#if busy === entry.id && restore.isPending}
									<span class="loading loading-spinner loading-xs"></span>
								{:else}
									<Undo2 class="h-3.5 w-3.5" />
								{/if}
								Restore
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>

		<p class="text-base-content/40 text-xs">
			History is kept for 30 days. A change nobody has objected to in a month is a change they
			wanted, and the snapshots are large enough that keeping them forever would dwarf the database.
		</p>
	{/if}
</div>
