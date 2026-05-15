export const ADMIN_BASE =
  process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

export async function adminFetch<T = any>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${ADMIN_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} · ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export async function adminFetchSafe<T = any>(
  path: string,
  fallback: T,
  init?: RequestInit
): Promise<T> {
  try {
    return await adminFetch<T>(path, init);
  } catch {
    return fallback;
  }
}
