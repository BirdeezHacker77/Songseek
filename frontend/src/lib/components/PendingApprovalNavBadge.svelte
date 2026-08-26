<script lang="ts">
	import { getPendingApprovalCountQuery } from '$lib/queries/following/AdminApprovalsQueries.svelte';

	const query = getPendingApprovalCountQuery(() => true);
	const count = $derived(query.data?.count ?? 0);
</script>

<!-- Anchored left so it clears the active-downloads badge: both now sit on the
     Requests icon, since Approvals is no longer a nav item of its own. -->
{#if count > 0}
	<span
		class="absolute -top-2 -left-2 badge badge-warning badge-xs h-4 w-4 p-0 text-[10px] font-bold"
		aria-label="{count} pending approvals"
	>
		{count > 9 ? '9+' : count}
	</span>
{/if}
