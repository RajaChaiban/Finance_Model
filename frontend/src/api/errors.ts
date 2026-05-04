/**
 * Shared error-message formatter for FastAPI responses.
 *
 * FastAPI returns 4xx errors with a ``detail`` field whose shape varies:
 *   - 400/500 from app code: ``detail: "Pricing failed: ..."`` (string)
 *   - 422 from pydantic validation: ``detail: [{loc, msg, type}, ...]`` (array)
 *   - sometimes the body has no ``detail`` at all (raw FastAPI default)
 *
 * Previously every call site did ``throw new Error(error.detail || "...")``.
 * When ``detail`` was an array, JS coerced it to a string and the user got
 * the literal text "[object Object]" in their toast. This formatter handles
 * all three shapes uniformly and produces something a human can read.
 */

interface PydanticError {
  loc: (string | number)[];
  msg: string;
  type?: string;
  input?: unknown;
}

function isPydanticErrorArray(value: unknown): value is PydanticError[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (item) =>
        item != null &&
        typeof item === "object" &&
        "msg" in (item as Record<string, unknown>) &&
        "loc" in (item as Record<string, unknown>),
    )
  );
}

/**
 * Turn a FastAPI ``detail`` payload into a single human-readable string.
 *
 * Priority:
 *   1. If detail is a non-empty pydantic validation array, format each error
 *      as ``"<field>: <msg>"`` and join with semicolons.
 *   2. If detail is a string, return it verbatim.
 *   3. Otherwise fall back to the supplied default.
 */
export function formatErrorDetail(detail: unknown, fallback: string): string {
  if (isPydanticErrorArray(detail)) {
    return detail
      .map((e) => {
        const path = e.loc.filter((p) => p !== "body").join(".") || "request";
        return `${path}: ${e.msg}`;
      })
      .join("; ");
  }
  if (typeof detail === "string" && detail.length > 0) {
    return detail;
  }
  return fallback;
}

/**
 * Build a human-readable Error from a non-OK ``Response``. Handles both
 * JSON-with-detail and non-JSON error bodies (e.g. proxy 502s).
 */
export async function errorFromResponse(
  response: Response,
  fallbackPrefix: string,
): Promise<Error> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const detail =
    body != null && typeof body === "object" && "detail" in (body as Record<string, unknown>)
      ? (body as Record<string, unknown>).detail
      : null;
  return new Error(formatErrorDetail(detail, `${fallbackPrefix} (${response.status})`));
}
