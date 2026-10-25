import { clientInstance } from "./client-output.js";
export async function api(path, { method = "GET", body, signal } = {}) {
  const multipart = body instanceof FormData;
  const response = await fetch(`/api/v1/${path}`, {
    method,
    signal,
    credentials: "same-origin",
    headers:
      body === undefined || multipart
        ? { "X-Client-Instance": clientInstance() }
        : { "Content-Type": "application/json", "X-Client-Instance": clientInstance() },
    body: multipart
      ? body
      : body === undefined
        ? undefined
        : JSON.stringify(body),
  });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok)
    throw Object.assign(new Error(
      payload.error?.message ||
        payload.detail ||
        `Request failed (${response.status})`,
    ), { status: response.status, code: payload.error?.code });
  return payload;
}
