<script lang="ts">
	import { ArrowLeft, ArrowRight, Disc3, ExternalLink, Search, ShieldCheck } from 'lucide-svelte';

	import AlbumImage from '$lib/components/AlbumImage.svelte';
	import { API } from '$lib/constants';
	import { getReleaseEditionSearchQuery } from '$lib/queries/library/LibraryEditionQueries.svelte';
	import { authStore } from '$lib/stores/authStore.svelte';

	interface Props {
		albumId: string;
		artistName: string;
		albumTitle: string;
		oncheck: (releaseMbid: string) => void | Promise<void>;
		checking?: boolean;
	}

	let { albumId, artistName, albumTitle, oncheck, checking = false }: Props = $props();
	let queryInput = $state('');
	let submittedQuery = $state('');
	let initialized = $state(false);
	let offset = $state(0);
	let advancedValue = $state('');
	let advancedAttempted = $state(false);

	const searchQuery = getReleaseEditionSearchQuery(
		() => authStore.user?.id,
		() => albumId,
		() => submittedQuery,
		() => offset
	);
	const result = $derived(searchQuery.data);
	const hasPrevious = $derived(offset > 0);
	const hasNext = $derived(Boolean(result && result.offset + result.items.length < result.total));
	const musicBrainzSearchUrl = $derived(
		`https://musicbrainz.org/search?query=${encodeURIComponent(submittedQuery)}&type=release&method=indexed`
	);
	const parsedAdvancedMbid = $derived(parseMusicBrainzReleaseId(advancedValue));

	$effect(() => {
		if (initialized) return;
		queryInput = `${artistName} ${albumTitle}`.trim();
		submittedQuery = queryInput;
		initialized = true;
	});

	export function parseMusicBrainzReleaseId(value: string): string | null {
		const normalized = value.trim().toLowerCase();
		const match = normalized.match(
			/^(?:https?:\/\/(?:www\.)?musicbrainz\.org\/release\/)?([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})(?:[/?#].*)?$/
		);
		return match?.[1] ?? null;
	}

	function search(): void {
		const normalized = queryInput.trim();
		if (!normalized) return;
		offset = 0;
		submittedQuery = normalized;
	}

	function checkAdvanced(): void {
		advancedAttempted = true;
		if (parsedAdvancedMbid) void oncheck(parsedAdvancedMbid);
	}
</script>

<section class="edition-finder" aria-labelledby="edition-finder-title">
	<div class="edition-finder-heading">
		<div>
			<p class="identification-kicker">MusicBrainz edition finder</p>
			<h3 id="edition-finder-title" class="hero-title text-xl font-bold">Search exact releases</h3>
		</div>
		<span class="edition-finder-suggestion">Suggestions only</span>
	</div>

	<form
		class="edition-finder-search"
		onsubmit={(event) => {
			event.preventDefault();
			search();
		}}
	>
		<label class="input input-bordered flex flex-1 items-center gap-2">
			<Search class="h-4 w-4 text-base-content/45" />
			<span class="sr-only">Search MusicBrainz releases</span>
			<input bind:value={queryInput} autocomplete="off" class="min-w-0 flex-1" />
		</label>
		<button class="btn btn-primary" type="submit" disabled={!queryInput.trim()}>Search</button>
	</form>

	{#if searchQuery.isLoading || searchQuery.isFetching}
		<div class="edition-finder-list" aria-live="polite" aria-label="Loading MusicBrainz editions">
			{#each Array(3) as _, index (index)}<div class="skeleton h-24 rounded-xl"></div>{/each}
		</div>
	{:else if searchQuery.isError}
		<div class="edition-finder-state" role="alert">
			<Disc3 class="h-7 w-7 text-error" />
			<div>
				<strong>MusicBrainz is unavailable</strong>
				<p>No album evidence was changed. Retry here or open this search on MusicBrainz.</p>
			</div>
			<button
				class="btn btn-outline btn-sm ml-auto"
				disabled={searchQuery.isFetching}
				onclick={() => void searchQuery.refetch()}>Retry</button
			>
		</div>
	{:else if result && result.items.length === 0}
		<div class="edition-finder-state" aria-live="polite">
			<Disc3 class="h-7 w-7" />
			<div>
				<strong>No editions found</strong>
				<p>Adjust the artist, title, year, label, or catalogue number.</p>
			</div>
		</div>
	{:else if result}
		<div class="edition-finder-list" aria-live="polite">
			{#each result.items as edition (edition.release_mbid)}
				<article class="edition-finder-row">
					<AlbumImage
						customUrl={API.library.exactReleaseArtwork(edition.release_mbid)}
						alt={`Cover for ${edition.title}`}
						size="sm"
						className="h-16 w-16 border border-base-content/10"
						retryOnError={false}
					/>
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-baseline gap-x-2">
							<strong>{edition.title}</strong>{#if edition.belongs_to_current_release_group}<span
									class="badge badge-primary badge-sm">Current release group</span
								>{/if}
						</div>
						<p class="text-sm text-base-content/60">
							{edition.artist_name || 'Unknown artist'} · {[
								edition.date,
								edition.country,
								edition.status
							]
								.filter(Boolean)
								.join(' · ') || 'Undated edition'}
						</p>
						<p class="mt-1 text-xs text-base-content/50">
							{[
								edition.media_formats.join(' + '),
								edition.packaging,
								edition.disc_count
									? `${edition.disc_count} disc${edition.disc_count === 1 ? '' : 's'}`
									: null,
								edition.track_count ? `${edition.track_count} tracks` : null,
								edition.label,
								edition.catalogue_number,
								edition.barcode
							]
								.filter(Boolean)
								.join(' · ')}
						</p>
						{#if edition.disambiguation}<p class="mt-1 text-xs italic text-base-content/55">
								{edition.disambiguation}
							</p>{/if}
					</div>
					<div class="flex shrink-0 flex-col items-end gap-1">
						<span class="text-xs text-base-content/45">{edition.score}% match</span><button
							class="btn btn-outline btn-sm"
							disabled={checking}
							onclick={() => void oncheck(edition.release_mbid)}
							>Check this edition <ArrowRight class="h-4 w-4" /></button
						>
					</div>
				</article>
			{/each}
		</div>
	{/if}

	<div class="edition-finder-footer">
		<div class="join">
			<button
				class="btn btn-sm join-item"
				disabled={!hasPrevious || searchQuery.isFetching}
				onclick={() => (offset = Math.max(0, offset - 12))}
				><ArrowLeft class="h-4 w-4" /> Previous</button
			>
			<button
				class="btn btn-sm join-item"
				disabled={!hasNext || searchQuery.isFetching}
				onclick={() => (offset += 12)}>Next <ArrowRight class="h-4 w-4" /></button
			>
		</div>
		<a class="btn btn-ghost btn-sm" href={musicBrainzSearchUrl} target="_blank" rel="noreferrer"
			>Open this search on MusicBrainz <ExternalLink class="h-4 w-4" /></a
		>
	</div>

	<details class="edition-finder-advanced">
		<summary>Already know the release? Paste a UUID or canonical MusicBrainz URL</summary>
		<div class="mt-3 flex flex-col gap-2 sm:flex-row">
			<label class="input input-bordered flex flex-1 items-center gap-2"
				><ShieldCheck class="h-4 w-4" /><span class="sr-only">MusicBrainz release UUID or URL</span
				><input
					bind:value={advancedValue}
					class="min-w-0 flex-1 font-mono text-sm"
					placeholder="https://musicbrainz.org/release/…"
				/></label
			>
			<button class="btn btn-outline" disabled={checking} onclick={checkAdvanced}
				>Check exact release</button
			>
		</div>
		{#if advancedAttempted && !parsedAdvancedMbid}<p class="mt-2 text-sm text-error" role="alert">
				Enter a MusicBrainz release UUID or canonical release URL.
			</p>{/if}
	</details>
</section>
