// Typed fetch client for the FastAPI /api/v1 endpoints plus money/time formatting helpers.
/** Error thrown for non-2xx API responses; carries the server error code and HTTP status. */
export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}
/** Fetch JSON from /api/v1 with credentials; throws ApiError on failure. */
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { ...options, headers: { "Content-Type": "application/json", ...options?.headers }, credentials: "same-origin" });
  const data = await response.json();
  if (!response.ok) throw new ApiError(data.code, data.message || "Request failed", response.status);
  return data as T;
}
/** Build a JSON request init (method defaults to POST) for use with api(). */
export function mutation<T>(path: string, body?: unknown, method = "POST") {
  return api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}
/** Format integer minor units (cents) as USD. */
export function money(minor: number) { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(minor / 100); }
/** Format an ISO timestamp in the given IANA timezone (date-only when date is true). */
export function time(iso: string, timezone: string, date = false) {
  return new Intl.DateTimeFormat("en-US", { timeZone: timezone, ...(date ? { month: "short", day: "numeric" } : {}), hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}
/** Format a Date as a datetime-local input value in the given timezone. */
export function localInput(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("sv-SE", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  const value = (key: string) => parts.find(p => p.type === key)?.value;
  return `${value("year")}-${value("month")}-${value("day")}T${value("hour")}:${value("minute")}`;
}
/** Convert a datetime-local input value interpreted in the given timezone to a UTC ISO string. */
export function localToISO(input: string, timezone: string) {
  if (/([+-]\d{2}:\d{2}|Z)$/.test(input)) return new Date(input).toISOString();
  const naive = Date.parse(`${input}:00Z`);
  if (!Number.isFinite(naive)) throw new Error("Choose a valid date and time");
  const candidates = new Set<string>();
  for (let offset = -14 * 60; offset <= 14 * 60; offset += 15) {
    const candidate = new Date(naive + offset * 60000);
    if (localInput(candidate, timezone) === input) candidates.add(candidate.toISOString());
  }
  if (candidates.size !== 1) throw new Error(candidates.size ? "This time occurs twice during daylight saving. Enter an ISO time with its offset, such as 2026-11-01T01:30:00-04:00." : "This local time does not exist. Choose another time.");
  return [...candidates][0];
}
