import { redirect } from '@sveltejs/kit';

/**
 * Downloads was folded into Requests, which now covers the whole lifecycle:
 * what you asked for, what is transferring, and what already landed.
 *
 * The route stays as a redirect rather than being deleted so existing
 * bookmarks, in-app links and the `?tab=import` deep link keep working. The
 * Requests page maps the old tab names onto the sections that replaced them.
 */
export function load({ url }): never {
	const tab = url.searchParams.get('tab');
	redirect(307, tab ? `/requests?tab=${encodeURIComponent(tab)}` : '/requests');
}
