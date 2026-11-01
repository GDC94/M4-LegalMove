/** The typed client of the LegalMove HTTP API: every answer passes a Zod schema first. */
import type { z } from "zod";
import {
  type AnalysisBody,
  analysisBodySchema,
  analysisCreatedSchema,
  apiProblemSchema,
  type ProgressEvent,
} from "./contract";
import { ApiError, ContractError, StreamInterruptedError } from "./errors";
import { readProgressEvents } from "./sse";

export type ImagePair = { original: File; amendment: File };

/** `finished`: the `job` event arrived. `unavailable`: the stream kept failing; poll instead. */
export type StreamOutcome = "finished" | "unavailable";

export type StreamOptions = {
  onEvent: (event: ProgressEvent) => void;
  signal?: AbortSignal;
};

export type ApiClient = {
  createAnalysis(pair: ImagePair): Promise<{ id: string }>;
  getAnalysis(id: string): Promise<AnalysisBody>;
  streamAnalysisEvents(id: string, options: StreamOptions): Promise<StreamOutcome>;
};

export type ApiClientConfig = {
  baseUrl: string;
  apiKey: string;
  /** One wait per reconnection; three entries mean three retries before falling back. */
  retryDelaysMs?: readonly number[];
  /** Silence after which a connection counts as dead and is retried. */
  streamIdleTimeoutMs?: number;
};

const DEFAULT_RETRY_DELAYS_MS = [500, 1000, 2000] as const;
// Three times the server's 15 s keep-alive: a half-open connection never closes on its own.
const DEFAULT_STREAM_IDLE_TIMEOUT_MS = 45_000;

export function createApiClient(config: ApiClientConfig): ApiClient {
  const retryDelaysMs = config.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
  const idleTimeoutMs = config.streamIdleTimeoutMs ?? DEFAULT_STREAM_IDLE_TIMEOUT_MS;
  const base = config.baseUrl.endsWith("/") ? config.baseUrl : `${config.baseUrl}/`;
  const url = (path: string) => new URL(path, base).toString();
  const headers = (extra: Record<string, string> = {}) => ({
    "X-API-Key": config.apiKey,
    ...extra,
  });

  async function createAnalysis(pair: ImagePair) {
    const form = new FormData();
    form.append("original", pair.original);
    form.append("amendment", pair.amendment);
    const response = await fetch(url("analyses"), {
      method: "POST",
      headers: headers(),
      body: form,
    });
    return parseJson(response, analysisCreatedSchema);
  }

  async function getAnalysis(id: string) {
    const response = await fetch(url(`analyses/${encodeURIComponent(id)}`), {
      headers: headers(),
    });
    return parseJson(response, analysisBodySchema);
  }

  async function openStream(id: string, cursor: Cursor, options: StreamOptions) {
    const extra: Record<string, string> = { Accept: "text/event-stream" };
    if (cursor.lastEventId !== undefined) extra["Last-Event-ID"] = cursor.lastEventId;
    let response: Response;
    try {
      response = await fetch(url(`analyses/${encodeURIComponent(id)}/events`), {
        headers: headers(extra),
        signal: options.signal,
      });
    } catch (error) {
      throw options.signal?.aborted ? error : new StreamInterruptedError("connection failed");
    }
    if (response.status >= 500)
      throw new StreamInterruptedError(`server answered ${response.status}`);
    if (!response.ok) throw await readProblem(response);
    if (response.body === null) throw new StreamInterruptedError("the stream has no body");
    return withIdleTimeout(response.body, idleTimeoutMs);
  }

  /** One connection: delivers its events; true when the terminal `job` event arrived. */
  async function followOnce(id: string, cursor: Cursor, options: StreamOptions) {
    const body = await openStream(id, cursor, options);
    try {
      for await (const frame of readProgressEvents(body)) {
        cursor.lastEventId = frame.id ?? String(frame.event.seq);
        cursor.delivered = true;
        options.onEvent(frame.event);
        if (frame.event.kind === "job") return true;
      }
    } catch (error) {
      if (error instanceof ContractError || options.signal?.aborted) throw error;
      throw new StreamInterruptedError("the stream broke");
    }
    return false;
  }

  async function streamAnalysisEvents(id: string, options: StreamOptions): Promise<StreamOutcome> {
    const cursor: Cursor = { lastEventId: undefined, delivered: false };
    let failures = 0;
    for (;;) {
      cursor.delivered = false;
      try {
        if (await followOnce(id, cursor, options)) return "finished";
      } catch (error) {
        if (!(error instanceof StreamInterruptedError)) throw error;
      }
      // A connection that delivered something was progress, so only consecutive failures count.
      failures = cursor.delivered ? 1 : failures + 1;
      const delay = retryDelaysMs[failures - 1];
      if (delay === undefined) return "unavailable";
      await sleep(delay, options.signal);
    }
  }

  return { createAnalysis, getAnalysis, streamAnalysisEvents };
}

type Cursor = { lastEventId: string | undefined; delivered: boolean };

async function parseJson<T>(response: Response, schema: z.ZodType<T>): Promise<T> {
  if (!response.ok) throw await readProblem(response);
  let json: unknown;
  try {
    json = await response.json();
  } catch {
    throw new ContractError("the API answered with a body that is not JSON");
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) throw new ContractError("the API answered outside its contract");
  return parsed.data;
}

/** Every refusal carries `{detail}`; a body without it still yields a readable error. */
async function readProblem(response: Response): Promise<ApiError> {
  const json: unknown = await response.json().catch(() => undefined);
  const parsed = apiProblemSchema.safeParse(json);
  const detail = parsed.success ? parsed.data.detail : response.statusText || "request refused";
  return new ApiError(response.status, detail);
}

/** Errors the stream when no chunk arrives within `ms`, so a silent connection is retried. */
function withIdleTimeout(body: ReadableStream<Uint8Array>, ms: number): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  return new ReadableStream<Uint8Array>(
    {
      async pull(controller) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        const silence = new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => reject(new StreamInterruptedError("the stream went silent")),
            ms,
          );
        });
        try {
          const { done, value } = await Promise.race([reader.read(), silence]);
          if (done) controller.close();
          else controller.enqueue(value);
        } catch (error) {
          reader.cancel(error).catch(() => {});
          throw error;
        } finally {
          clearTimeout(timer);
        }
      },
      cancel: (reason) => reader.cancel(reason),
    },
    { highWaterMark: 0 },
  );
}

/** Exported for its tests: the wait between reconnections, cut short by an abort. */
export function sleep(ms: number, signal: AbortSignal | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
