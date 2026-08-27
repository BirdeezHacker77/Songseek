import { pageFetch } from '$lib/utils/navigationAbort';

/**
 * Every request gets a deadline unless it explicitly opts out with `timeoutMs: 0`.
 *
 * Without one, a connection that stalls without closing leaves the promise
 * pending forever. Nothing upstream rescues it: the query stays `pending`, so
 * `isPending` skeletons never clear, `retry: false` means it is never retried,
 * and there is no error for a page to show a retry button for. The request just
 * hangs until the tab regains focus and `refetchOnWindowFocus` happens to start
 * a fresh one - which is why it reads as an intermittent, self-healing hang.
 *
 * A minute is deliberately generous: long work in this app is job-based (start,
 * then poll), so no legitimate request should come near it. The point is to turn
 * "forever" into a normal error that the existing retry paths already handle.
 */
const DEFAULT_TIMEOUT_MS = 60_000;

/**
 * Transfers that move real bytes get much longer: uploads, and any `raw`
 * response whose body the caller streams itself after this function returns -
 * the deadline still covers that read, because it aborts the whole request.
 */
const STREAMING_TIMEOUT_MS = 10 * 60_000;
import { getApiUrl } from '$lib/api/api-utils';
import { browser } from '$app/environment';
import { authStore } from '$lib/stores/authStore.svelte';

export class ApiError extends Error {
	readonly status: number;
	readonly code: string;
	readonly details: unknown;

	constructor(status: number, message: string, code = '', details: unknown = null) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.code = code;
		this.details = details;
	}
}

/**
 * Thrown when a request fails with 401 while a session was active. The client
 * has already cleared the store and triggered a hard redirect to /login;
 * callers can use `instanceof SessionExpiredError` to suppress error UI that
 * would otherwise flash during navigation.
 */
export class SessionExpiredError extends ApiError {
	constructor(message = 'Session expired') {
		super(401, message, 'session_expired');
		this.name = 'SessionExpiredError';
	}
}

export type TransportErrorCode = 'TRANSPORT_TIMEOUT' | 'TRANSPORT_ABORTED' | 'TRANSPORT_NETWORK';

export class TransportError extends ApiError {
	readonly method: string;
	readonly path: string;

	constructor(code: TransportErrorCode, method: string, path: string) {
		const message =
			code === 'TRANSPORT_TIMEOUT'
				? 'The request timed out'
				: code === 'TRANSPORT_ABORTED'
					? 'The request was cancelled'
					: 'The server could not be reached';
		super(0, message, code, { method, path });
		this.name = 'TransportError';
		this.method = method;
		this.path = path;
	}
}

interface RequestOptions extends Omit<RequestInit, 'method' | 'body'> {
	signal?: AbortSignal;
	raw?: boolean;
	cache?: RequestCache;
	/** Deadline in ms. Omit for the default; pass 0 to wait indefinitely. */
	timeoutMs?: number;
}

interface DeleteRequestOptions extends RequestOptions {
	body?: unknown;
}

async function handleResponse<T = void>(res: Response): Promise<T> {
	if (!res.ok) {
		// Session expired mid-use: hard redirect so layout re-initialises cleanly
		if (res.status === 401 && browser && authStore.isAuthenticated) {
			authStore.clear();
			window.location.href = '/login';
			throw new SessionExpiredError();
		}

		const text = await res.text().catch(() => '');
		let message = text || `Request failed with status ${res.status}`;
		let code = '';
		let details: unknown = null;
		try {
			const parsed = JSON.parse(text);
			if (parsed?.error?.message) {
				message = parsed.error.message;
				code = parsed.error.code ?? '';
				details = parsed.error.details ?? null;
			} else if (parsed?.detail) {
				message = parsed.detail;
			}
		} catch {
			// preserve non-JSON error bodies
		}
		throw new ApiError(res.status, message, code, details);
	}

	if (res.status === 204 || res.headers.get('content-length') === '0') {
		return undefined as T;
	}

	const text = await res.text().catch(() => '');
	if (text.trim() === '') {
		return undefined as T;
	}

	try {
		return JSON.parse(text) as T;
	} catch {
		throw new ApiError(res.status, 'Failed to parse response JSON');
	}
}

type FetchFn = typeof fetch;

interface ApiClient {
	get<T = unknown>(url: string, opts?: RequestOptions): Promise<T>;
	post<T = unknown>(url: string, body?: unknown, opts?: RequestOptions): Promise<T>;
	put<T = unknown>(url: string, body?: unknown, opts?: RequestOptions): Promise<T>;
	patch<T = unknown>(url: string, body?: unknown, opts?: RequestOptions): Promise<T>;
	delete<T = void>(url: string, opts?: DeleteRequestOptions): Promise<T>;
	head(url: string, opts?: RequestOptions): Promise<Response>;
	upload<T = unknown>(url: string, body: FormData, opts?: RequestOptions): Promise<T>;
}

function createClient(fetchFn: FetchFn): ApiClient {
	function transportPath(url: string): string {
		try {
			return new URL(url, 'http://songseek.invalid').pathname;
		} catch {
			return url.split('?', 1)[0] ?? url;
		}
	}

	async function request<T>(
		method: string,
		url: string,
		body?: unknown,
		opts?: RequestOptions
	): Promise<T> {
		const { raw, timeoutMs, signal, ...fetchOpts } = opts ?? {};
		// credentials: 'include' sends the httpOnly session cookie cross-origin (dev proxy)
		// An explicit controller rather than AbortSignal.timeout, because that one
		// keeps a live timer for its full duration whether or not the request is
		// still running. At one per request that accumulates fast; this version is
		// cleared the moment the response is consumed.
		const deadline = timeoutMs ?? (raw ? STREAMING_TIMEOUT_MS : DEFAULT_TIMEOUT_MS);
		const deadlineController = deadline > 0 ? new AbortController() : undefined;
		const deadlineTimer = deadlineController
			? setTimeout(() => deadlineController.abort(), deadline)
			: undefined;
		const deadlineSignal = deadlineController?.signal;
		const requestSignal =
			signal && deadlineSignal
				? AbortSignal.any([signal, deadlineSignal])
				: (signal ?? deadlineSignal);
		const init: RequestInit = {
			method,
			credentials: 'include',
			...fetchOpts,
			signal: requestSignal
		};

		if (body !== undefined && body !== null) {
			if (body instanceof FormData) {
				// Do not set Content-Type, the browser sets multipart/form-data with boundary automatically
				init.body = body;
			} else {
				const headers = new Headers(init.headers as HeadersInit | undefined);
				headers.set('Content-Type', 'application/json');
				init.headers = headers;
				init.body = JSON.stringify(body);
			}
		}

		const requestUrl = getApiUrl(url);

		try {
			let res: Response;
			try {
				res = await fetchFn(requestUrl, init);
			} catch (cause) {
				const timedOut = deadlineSignal?.aborted === true && signal?.aborted !== true;
				const aborted =
					!timedOut &&
					((cause instanceof DOMException && cause.name === 'AbortError') ||
						requestSignal?.aborted === true);
				throw new TransportError(
					timedOut ? 'TRANSPORT_TIMEOUT' : aborted ? 'TRANSPORT_ABORTED' : 'TRANSPORT_NETWORK',
					method,
					transportPath(url)
				);
			}

			// A raw response hands the undrained body to the caller, so its deadline
			// has to outlive this function - that read is exactly what it guards.
			if (raw) return res as unknown as T;
			return await handleResponse<T>(res);
		} finally {
			if (!raw && deadlineTimer !== undefined) clearTimeout(deadlineTimer);
		}
	}

	return {
		get: <T = unknown>(url: string, opts?: RequestOptions) =>
			request<T>('GET', url, undefined, opts),
		post: <T = unknown>(url: string, body?: unknown, opts?: RequestOptions) =>
			request<T>('POST', url, body, opts),
		put: <T = unknown>(url: string, body?: unknown, opts?: RequestOptions) =>
			request<T>('PUT', url, body, opts),
		patch: <T = unknown>(url: string, body?: unknown, opts?: RequestOptions) =>
			request<T>('PATCH', url, body, opts),
		delete: <T = void>(url: string, opts?: DeleteRequestOptions) => {
			const { body, ...requestOptions } = opts ?? {};
			return request<T>('DELETE', url, body, requestOptions);
		},
		head: (url: string, opts?: RequestOptions) =>
			request<Response>('HEAD', url, undefined, { ...opts, raw: true }),
		upload: <T = unknown>(url: string, body: FormData, opts?: RequestOptions) =>
			request<T>('POST', url, body, { timeoutMs: STREAMING_TIMEOUT_MS, ...opts })
	};
}

const navClient = createClient(pageFetch);
const globalClient = createClient((...args) => globalThis.fetch(...args));

export const api = Object.assign(navClient, { global: globalClient });
