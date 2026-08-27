<script lang="ts">
	import { AudioLines, Mic2, RefreshCw } from 'lucide-svelte';

	import {
		getDownloadEnrichmentQuery,
		saveDownloadEnrichment
	} from '$lib/queries/downloads/DownloadClientsQueries.svelte';
	import { authStore } from '$lib/stores/authStore.svelte';
	import { toastStore } from '$lib/stores/toast';
	import type { DownloadEnrichmentSettings } from '$lib/types';

	const query = getDownloadEnrichmentQuery(() => authStore.isAdmin);
	const save = saveDownloadEnrichment();

	let draft = $state<DownloadEnrichmentSettings | null>(null);

	$effect(() => {
		if (query.data && draft === null) {
			draft = structuredClone($state.snapshot(query.data)) as DownloadEnrichmentSettings;
		}
	});

	const dirty = $derived(
		draft !== null &&
			query.data !== undefined &&
			JSON.stringify($state.snapshot(draft)) !== JSON.stringify(query.data)
	);

	async function handleSave(): Promise<void> {
		if (!draft) return;
		try {
			await save.mutateAsync($state.snapshot(draft) as DownloadEnrichmentSettings);
			toastStore.show({ message: 'Enrichment settings saved', type: 'success' });
		} catch (error) {
			toastStore.show({
				message: error instanceof Error ? error.message : 'Could not save those settings',
				type: 'error'
			});
		}
	}
</script>

<div class="space-y-6">
	<div>
		<h2 class="text-xl font-bold">Enrichment</h2>
		<p class="text-base-content/50 mt-0.5 text-sm">
			What gets added to a track on its way into your library. These apply to new downloads only -
			your existing files are never touched, and changing them needs no dry run.
		</p>
	</div>

	{#if !authStore.isAdmin}
		<div class="alert alert-info">Only an administrator can change enrichment settings.</div>
	{:else if query.isLoading}
		<div class="space-y-3">
			<div class="skeleton h-40 w-full rounded-box"></div>
			<div class="skeleton h-24 w-full rounded-box"></div>
		</div>
	{:else if query.isError}
		<div class="alert alert-error">Could not load enrichment settings.</div>
	{:else if draft}
		<section class="card border border-base-300 bg-base-200/55">
			<div class="card-body gap-4">
				<div class="flex items-start gap-3">
					<Mic2 class="mt-0.5 h-5 w-5 text-primary" aria-hidden="true" />
					<div class="min-w-0 flex-1">
						<h3 class="font-semibold">Lyrics</h3>
						<p class="text-base-content/50 text-sm">
							Looked up by artist, title and duration, so a track only gets lyrics that actually
							match it.
						</p>
					</div>
				</div>

				<label class="flex cursor-pointer items-center gap-3">
					<input
						type="checkbox"
						class="toggle toggle-primary toggle-sm"
						bind:checked={draft.lyrics.enabled}
					/>
					<span class="font-medium">Grab lyrics on future downloads</span>
				</label>

				<fieldset class="grid gap-3 pl-1" disabled={!draft.lyrics.enabled}>
					<label class="flex items-center gap-3 text-sm">
						<span class="text-base-content/60 w-28 shrink-0">Source</span>
						<select class="select select-bordered select-sm" bind:value={draft.lyrics.provider}>
							<option value="lrclib">LRCLIB</option>
						</select>
					</label>

					<label class="flex cursor-pointer items-start gap-3">
						<input
							type="checkbox"
							class="checkbox checkbox-sm mt-0.5"
							bind:checked={draft.lyrics.write_lrc_file}
						/>
						<span
							><strong class="text-sm">Save a .lrc file next to the song</strong>
							<small class="text-base-content/50 block"
								>What most players and scanners look for, and what a library built by other download
								tools already uses.</small
							></span
						>
					</label>

					<label class="flex cursor-pointer items-start gap-3">
						<input
							type="checkbox"
							class="checkbox checkbox-sm mt-0.5"
							bind:checked={draft.lyrics.embed_in_tags}
						/>
						<span
							><strong class="text-sm">Also write them into the song's tags</strong>
							<small class="text-base-content/50 block"
								>Travels with the file if it is ever moved without its .lrc.</small
							></span
						>
					</label>

					<label class="flex cursor-pointer items-start gap-3">
						<input
							type="checkbox"
							class="checkbox checkbox-sm mt-0.5"
							bind:checked={draft.lyrics.prefer_synced}
						/>
						<span
							><strong class="text-sm">Prefer time-synced lyrics</strong>
							<small class="text-base-content/50 block"
								>Timestamped so they scroll with playback. Plain lyrics are used when no synced
								version exists.</small
							></span
						>
					</label>
				</fieldset>

				<p class="text-base-content/40 text-xs">
					A track with no match on LRCLIB imports as normal - missing lyrics never hold up a
					download.
				</p>
			</div>
		</section>

		<section class="card border border-base-300 bg-base-200/55">
			<div class="card-body gap-4">
				<div class="flex items-start gap-3">
					<AudioLines class="mt-0.5 h-5 w-5 text-primary" aria-hidden="true" />
					<div class="min-w-0 flex-1">
						<h3 class="font-semibold">ReplayGain</h3>
						<p class="text-base-content/50 text-sm">
							Measures loudness so albums play at an even volume instead of one being far louder
							than the next.
						</p>
					</div>
				</div>

				<label class="flex cursor-pointer items-center gap-3">
					<input
						type="checkbox"
						class="toggle toggle-primary toggle-sm"
						bind:checked={draft.replaygain.enabled}
					/>
					<span class="font-medium">Calculate ReplayGain on future downloads</span>
				</label>

				<fieldset class="pl-1" disabled={!draft.replaygain.enabled}>
					<label class="flex cursor-pointer items-start gap-3">
						<input
							type="checkbox"
							class="checkbox checkbox-sm mt-0.5"
							bind:checked={draft.replaygain.album_aware}
						/>
						<span
							><strong class="text-sm">Measure the album as a whole too</strong>
							<small class="text-base-content/50 block"
								>Keeps the quiet and loud tracks of one album in proportion to each other.</small
							></span
						>
					</label>
				</fieldset>

				<p class="text-base-content/40 text-xs">
					Analysis runs per track and is CPU-heavy, so a large download takes noticeably longer.
					Gains already present on a file are kept.
				</p>
			</div>
		</section>

		<section class="card border border-base-300 bg-base-200/55">
			<div class="card-body gap-4">
				<div class="flex items-start gap-3">
					<RefreshCw class="mt-0.5 h-5 w-5 text-primary" aria-hidden="true" />
					<div class="min-w-0 flex-1">
						<h3 class="font-semibold">Media server refresh</h3>
						<p class="text-base-content/50 text-sm">
							Tells your media server a new album landed, so it appears without waiting for its own
							scan schedule.
						</p>
					</div>
				</div>

				<label class="flex cursor-pointer items-center gap-3">
					<input
						type="checkbox"
						class="toggle toggle-primary toggle-sm"
						bind:checked={draft.refresh.enabled}
					/>
					<span class="font-medium">Refresh after a download is imported</span>
				</label>

				<fieldset class="grid gap-3 pl-1" disabled={!draft.refresh.enabled}>
					<label class="flex cursor-pointer items-center gap-3">
						<input
							type="checkbox"
							class="checkbox checkbox-sm"
							bind:checked={draft.refresh.navidrome_enabled}
						/>
						<span class="text-sm font-medium">Navidrome</span>
					</label>
					<label class="flex cursor-pointer items-center gap-3">
						<input
							type="checkbox"
							class="checkbox checkbox-sm"
							bind:checked={draft.refresh.jellyfin_enabled}
						/>
						<span class="text-sm font-medium">Jellyfin</span>
					</label>
				</fieldset>

				<p class="text-base-content/40 text-xs">
					One refresh per import, not per track. Plex is not listed because it has no refresh
					support here yet. A server that is unreachable is skipped without failing the import.
				</p>
			</div>
		</section>

		<div class="flex items-center justify-end gap-3">
			{#if dirty}<span class="text-base-content/50 text-sm">Unsaved changes</span>{/if}
			<button
				class="btn btn-primary btn-sm"
				disabled={!dirty || save.isPending}
				onclick={handleSave}
			>
				{#if save.isPending}<span class="loading loading-spinner loading-xs"></span>{/if}
				Save
			</button>
		</div>
	{/if}
</div>
