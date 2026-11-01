import { HttpResponse, http } from "msw/http";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API,
  DONE_BODY,
  finished,
  JOB_ID,
  KEY,
  pngFile,
  sseBody,
  sseFrames,
  stage,
} from "@/test/fixtures";
import { server } from "@/test/server";
import { createApiClient, sleep } from "./client";
import type { ProgressEvent } from "./contract";
import { ApiError, ContractError } from "./errors";

const EVENTS = `${API}/analyses/:id/events`;
const client = createApiClient({ baseUrl: API, apiKey: KEY, retryDelaysMs: [0, 0, 0] });

function eventStream(text: string) {
  return new HttpResponse(sseBody(text), { headers: { "content-type": "text/event-stream" } });
}

async function follow() {
  const events: ProgressEvent[] = [];
  const outcome = await client.streamAnalysisEvents(JOB_ID, { onEvent: (e) => events.push(e) });
  return { outcome, events };
}

describe("createAnalysis", () => {
  it("posts both images as multipart under their roles with the API key", async () => {
    let seen: { key: string | null; original: string; amendment: string } | undefined;
    server.use(
      http.post(`${API}/analyses`, async ({ request }) => {
        const form = await request.formData();
        const name = (field: string) => (form.get(field) as File).name;
        seen = {
          key: request.headers.get("x-api-key"),
          original: name("original"),
          amendment: name("amendment"),
        };
        return HttpResponse.json({ id: JOB_ID }, { status: 202 });
      }),
    );

    const created = await client.createAnalysis({
      original: pngFile("contract.png"),
      amendment: pngFile("addendum.png"),
    });

    expect(created).toEqual({ id: JOB_ID });
    expect(seen).toEqual({ key: KEY, original: "contract.png", amendment: "addendum.png" });
  });

  it("turns a refusal into an ApiError with the API's own detail", async () => {
    server.use(
      http.post(`${API}/analyses`, () =>
        HttpResponse.json({ detail: "each image must be at most 10 bytes" }, { status: 413 }),
      ),
    );

    const refusal = client.createAnalysis({ original: pngFile(), amendment: pngFile() });

    await expect(refusal).rejects.toEqual(new ApiError(413, "each image must be at most 10 bytes"));
  });
});

describe("getAnalysis", () => {
  it("returns the validated body and rejects one outside the contract", async () => {
    server.use(http.get(`${API}/analyses/:id`, () => HttpResponse.json(DONE_BODY), { once: true }));
    expect(await client.getAnalysis(JOB_ID)).toEqual(DONE_BODY);

    server.use(http.get(`${API}/analyses/:id`, () => HttpResponse.json({ ...DONE_BODY, id: 7 })));
    await expect(client.getAnalysis(JOB_ID)).rejects.toBeInstanceOf(ContractError);
  });
});

describe("streamAnalysisEvents", () => {
  it("delivers each event once, in order, and finishes on the job event", async () => {
    let key: string | null = null;
    server.use(
      http.get(EVENTS, ({ request }) => {
        key = request.headers.get("x-api-key");
        return eventStream(
          `: keep-alive\n\n${sseFrames(stage(1, "parse_original_contract", "started"), finished(2, "done"))}`,
        );
      }),
    );

    const { outcome, events } = await follow();

    expect(outcome).toBe("finished");
    expect(events.map((event) => event.seq)).toEqual([1, 2]);
    expect(key).toBe(KEY);
  });

  it("reconnects with Last-Event-ID after a cut and never repeats an event", async () => {
    const lastIds: (string | null)[] = [];
    server.use(
      http.get(EVENTS, ({ request }) => {
        lastIds.push(request.headers.get("last-event-id"));
        if (lastIds.length === 1) {
          return eventStream(
            sseFrames(
              stage(1, "parse_original_contract", "started"),
              stage(2, "parse_original_contract", "done"),
            ),
          );
        }
        if (lastIds.length === 2) return HttpResponse.error();
        return eventStream(
          sseFrames(stage(3, "parse_amendment_contract", "started"), finished(4, "done")),
        );
      }),
    );

    const { outcome, events } = await follow();

    expect(outcome).toBe("finished");
    expect(lastIds).toEqual([null, "2", "2"]);
    expect(events.map((event) => event.seq)).toEqual([1, 2, 3, 4]);
  });

  it("gives up after three failed reconnections so the caller can poll", async () => {
    let attempts = 0;
    server.use(
      http.get(EVENTS, () => {
        attempts += 1;
        return new HttpResponse(null, { status: 502 });
      }),
    );

    const { outcome } = await follow();

    expect(outcome).toBe("unavailable");
    expect(attempts).toBe(4);
  });

  it("does not retry a contract error or a refusal", async () => {
    let attempts = 0;
    server.use(
      http.get(EVENTS, () => {
        attempts += 1;
        return eventStream('id: 1\ndata: {"seq":1,"kind":"stage"}\n\n');
      }),
    );
    await expect(follow()).rejects.toBeInstanceOf(ContractError);

    server.use(
      http.get(EVENTS, () => {
        attempts += 1;
        return HttpResponse.json({ detail: "analysis not found" }, { status: 404 });
      }),
    );
    await expect(follow()).rejects.toEqual(new ApiError(404, "analysis not found"));
    expect(attempts).toBe(2);
  });
});

describe("streamAnalysisEvents idle watchdog", () => {
  afterEach(() => vi.useRealTimers());

  /** A connection that sends its frames and then goes silent without closing. */
  function silentAfter(text: string) {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text));
      },
    });
    return new Response(body, { headers: { "content-type": "text/event-stream" } });
  }

  it("reconnects with Last-Event-ID once a connection stays silent past the idle timeout", async () => {
    vi.useFakeTimers();
    const lastIds: (string | null)[] = [];
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      lastIds.push(new Headers(init?.headers).get("last-event-id"));
      return lastIds.length === 1
        ? silentAfter(sseFrames(stage(1, "parse_original_contract", "started")))
        : new Response(sseBody(sseFrames(finished(2, "done"))));
    });
    const watched = createApiClient({
      baseUrl: API,
      apiKey: KEY,
      retryDelaysMs: [0, 0, 0],
      streamIdleTimeoutMs: 45_000,
    });
    let outcome: string | undefined;
    const following = watched.streamAnalysisEvents(JOB_ID, { onEvent: () => {} }).then((value) => {
      outcome = value;
    });

    await vi.advanceTimersByTimeAsync(44_999);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(outcome).toBeUndefined();

    await vi.advanceTimersByTimeAsync(1);
    // The silent connection is dropped; the fake clock runs a 0 ms retry wait at 1 ms.
    await vi.advanceTimersByTimeAsync(1);
    await following;
    expect(outcome).toBe("finished");
    expect(lastIds).toEqual([null, "1"]);
  });
});

describe("sleep", () => {
  it("drops its abort listener once the wait is over", async () => {
    const controller = new AbortController();
    const added = vi.spyOn(controller.signal, "addEventListener");
    const removed = vi.spyOn(controller.signal, "removeEventListener");

    await sleep(0, controller.signal);

    const listener = added.mock.calls[0]?.[1];
    expect(listener).toBeTypeOf("function");
    expect(removed).toHaveBeenCalledWith("abort", listener);
  });

  it("rejects with the abort reason when aborted mid-wait", async () => {
    const controller = new AbortController();
    const waiting = sleep(60_000, controller.signal);

    controller.abort(new Error("reset"));

    await expect(waiting).rejects.toThrow("reset");
  });
});
