// Request validation that reproduces the FastAPI/pydantic v2 behaviour of the Python
// relay closely enough that every client sees the same status codes and the same
// `{"detail": [...]}` 422 shape (type / loc / msg / input / ctx).

import { isSafePublicUrl } from "./url-safety";

export type Loc = Array<string | number>;
export interface ValidationIssue {
  type: string;
  loc: Loc;
  msg: string;
  input: unknown;
  ctx?: Record<string, unknown>;
}

export class RequestValidationError extends Error {
  constructor(public readonly issues: ValidationIssue[]) {
    super("request validation failed");
  }
}

type Obj = Record<string, unknown>;

export class Checker {
  readonly issues: ValidationIssue[] = [];

  issue(type: string, loc: Loc, msg: string, input: unknown, ctx?: Record<string, unknown>): void {
    const entry: ValidationIssue = { type, loc, msg, input };
    if (ctx) entry.ctx = ctx;
    this.issues.push(entry);
  }

  /** Required (no default) or optional string with pydantic length constraints. */
  str(
    source: Obj,
    field: string,
    locPrefix: Loc,
    opts: { required?: boolean; default?: string; min?: number; max?: number; inputOnMissing?: unknown } = {},
  ): string | undefined {
    const loc = [...locPrefix, field];
    if (!(field in source) || source[field] === undefined) {
      if (opts.required) {
        this.issue("missing", loc, "Field required", opts.inputOnMissing ?? source);
        return undefined;
      }
      return opts.default ?? "";
    }
    const value = source[field];
    if (typeof value !== "string") {
      this.issue("string_type", loc, "Input should be a valid string", value);
      return undefined;
    }
    // pydantic counts characters (code points), not UTF-16 units.
    const length = [...value].length;
    if (opts.min !== undefined && length < opts.min) {
      this.issue(
        "string_too_short",
        loc,
        `String should have at least ${opts.min} character${opts.min === 1 ? "" : "s"}`,
        value,
        { min_length: opts.min },
      );
      return undefined;
    }
    if (opts.max !== undefined && length > opts.max) {
      this.issue(
        "string_too_long",
        loc,
        `String should have at most ${opts.max} character${opts.max === 1 ? "" : "s"}`,
        value,
        { max_length: opts.max },
      );
      return undefined;
    }
    return value;
  }

  /** pydantic HttpUrl + the relay's public-host validator. Returns the normalised URL. */
  httpUrl(source: Obj, field: string, locPrefix: Loc): string | undefined {
    const loc = [...locPrefix, field];
    if (!(field in source) || source[field] === undefined) {
      this.issue("missing", loc, "Field required", source);
      return undefined;
    }
    const value = source[field];
    if (typeof value !== "string") {
      this.issue("url_type", loc, "URL input should be a string or URL", value);
      return undefined;
    }
    if ([...value].length > 2083) {
      this.issue("url_too_long", loc, "URL should have at most 2083 characters", value, { max_length: 2083 });
      return undefined;
    }
    let parsed: URL;
    try {
      parsed = new URL(value);
    } catch {
      const error = value.trim() === "" ? "input is empty" : "relative URL without a base";
      this.issue("url_parsing", loc, `Input should be a valid URL, ${error}`, value, { error });
      return undefined;
    }
    const scheme = parsed.protocol.replace(/:$/, "");
    if (scheme !== "http" && scheme !== "https") {
      this.issue("url_scheme", loc, "URL scheme should be 'http' or 'https'", value, {
        expected_schemes: "'http' or 'https'",
      });
      return undefined;
    }
    if (!parsed.hostname) {
      this.issue("url_parsing", loc, "Input should be a valid URL, empty host", value, { error: "empty host" });
      return undefined;
    }
    const normalised = parsed.href;
    if (!isSafePublicUrl(normalised)) {
      this.issue("value_error", loc, "Value error, url must be an http(s) link to a public host", value, {
        error: {},
      });
      return undefined;
    }
    return normalised;
  }

  /** pydantic lax-mode bool. */
  bool(source: Obj, field: string, locPrefix: Loc, fallback: boolean): boolean | undefined {
    const loc = [...locPrefix, field];
    if (!(field in source) || source[field] === undefined) return fallback;
    const value = source[field];
    if (typeof value === "boolean") return value;
    if (typeof value === "number") {
      if (value === 0 || value === 1) return value === 1;
      this.issue("bool_parsing", loc, "Input should be a valid boolean, unable to interpret input", value);
      return undefined;
    }
    if (typeof value === "string") {
      const v = value.trim().toLowerCase();
      if (["1", "on", "t", "true", "y", "yes"].includes(v)) return true;
      if (["0", "off", "f", "false", "n", "no"].includes(v)) return false;
      this.issue("bool_parsing", loc, "Input should be a valid boolean, unable to interpret input", value);
      return undefined;
    }
    this.issue("bool_type", loc, "Input should be a valid boolean", value);
    return undefined;
  }

  /** list[str] with an empty-list default. */
  strList(source: Obj, field: string, locPrefix: Loc): string[] | undefined {
    const loc = [...locPrefix, field];
    if (!(field in source) || source[field] === undefined) return [];
    const value = source[field];
    if (!Array.isArray(value)) {
      this.issue("list_type", loc, "Input should be a valid list", value);
      return undefined;
    }
    const out: string[] = [];
    let ok = true;
    value.forEach((item, index) => {
      if (typeof item !== "string") {
        this.issue("string_type", [...loc, index], "Input should be a valid string", item);
        ok = false;
      } else out.push(item);
    });
    return ok ? out : undefined;
  }

  /** Query int with ge/le bounds (FastAPI Query(ge=, le=)). */
  queryInt(params: URLSearchParams, field: string, fallback: number, ge: number, le: number): number | undefined {
    const loc = ["query", field];
    const raw = params.get(field);
    if (raw === null) return fallback;
    const text = raw.trim().replace(/_/g, "");
    let value: number | null = null;
    if (/^[+-]?\d+$/.test(text)) value = Number(text);
    else if (/^[+-]?\d+\.0*$/.test(text)) value = Number(text.split(".")[0]);
    if (value === null || !Number.isFinite(value)) {
      this.issue("int_parsing", loc, "Input should be a valid integer, unable to parse string as an integer", raw);
      return undefined;
    }
    if (value < ge) {
      this.issue("greater_than_equal", loc, `Input should be greater than or equal to ${ge}`, raw, { ge });
      return undefined;
    }
    if (value > le) {
      this.issue("less_than_equal", loc, `Input should be less than or equal to ${le}`, raw, { le });
      return undefined;
    }
    return value;
  }

  queryStr(params: URLSearchParams, field: string, opts: { required?: boolean; default?: string; max: number }): string | undefined {
    const raw = params.get(field);
    if (raw === null) {
      if (opts.required) {
        this.issue("missing", ["query", field], "Field required", null);
        return undefined;
      }
      return opts.default ?? "";
    }
    return this.str({ [field]: raw }, field, ["query"], { max: opts.max });
  }

  throwIfAny(): void {
    if (this.issues.length) throw new RequestValidationError(this.issues);
  }
}

/** Read a JSON object body the way FastAPI does for a single pydantic model parameter. */
export async function readJsonBody(request: Request): Promise<Obj> {
  const raw = await request.text();
  if (!raw) {
    throw new RequestValidationError([{ type: "missing", loc: ["body"], msg: "Field required", input: null }]);
  }
  const contentType = (request.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  const isJson =
    !contentType || contentType === "application/json" || (contentType.startsWith("application/") && contentType.endsWith("+json"));
  let body: unknown = raw;
  if (isJson) {
    try {
      body = JSON.parse(raw);
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      const position = Number(/position (\d+)/.exec(message)?.[1] ?? 0);
      throw new RequestValidationError([
        {
          type: "json_invalid",
          loc: ["body", position],
          msg: "JSON decode error",
          input: {},
          ctx: { error: err instanceof Error ? err.message : "Expecting value" },
        },
      ]);
    }
  }
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw new RequestValidationError([
      {
        type: "model_attributes_type",
        loc: ["body"],
        msg: "Input should be a valid dictionary or object to extract fields from",
        input: body,
      },
    ]);
  }
  return body as Obj;
}

/** SaveEventRequest.sound_id after-validator: short alphanumeric (Unicode-aware like str.isalnum). */
export function isPlausibleSoundId(value: string): boolean {
  return value.length > 0 && [...value].length <= 64 && /^[\p{L}\p{N}_-]+$/u.test(value);
}
