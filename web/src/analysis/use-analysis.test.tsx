import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient, StreamOptions, StreamOutcome } from "@/api/client";
import { ApiError } from "@/api/errors";
import { DONE_BODY, finished, JOB_ID, pngFile } from "@/test/fixtures";
import { useAnalysis } from "./use-analysis";

const PAIR = { original: pngFile("contract.png"), amendment: pngFile("addendum.png") };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** A client double: every call is recorded and answered by the test. */
function fakeClient() {
  const client = {
    createAnalysis: vi.fn<ApiClient["createAnalysis"]>(() => Promise.resolve({ id: JOB_ID })),
    getAnalysis: vi.fn<ApiClient["getAnalysis"]>(() => Promise.resolve(DONE_BODY)),
    streamAnalysisEvents: vi.fn<ApiClient["streamAnalysisEvents"]>(() =>
      Promise.resolve<StreamOutcome>("unavailable"),
    ),
  };
  return client;
}

function renderAnalysis(client: ApiClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useAnalysis(client, 5), { wrapper });
}

describe("useAnalysis", () => {
  it("posts once when the form is submitted twice in a row", async () => {
    const client = fakeClient();
    const { result } = renderAnalysis(client);

    act(() => {
      result.current.submit(PAIR);
      result.current.submit(PAIR);
    });

    await waitFor(() => expect(result.current.state.phase).toBe("done"));
    expect(client.createAnalysis).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({
      changes: DONE_BODY.changes,
      amendment_kind: "delta",
    });
  });

  it("keeps polling through a transient error and settles on the body", async () => {
    const client = fakeClient();
    client.getAnalysis
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockRejectedValueOnce(new ApiError(503, "busy"));
    const { result } = renderAnalysis(client);

    act(() => result.current.submit(PAIR));

    await waitFor(() => expect(result.current.state.phase).toBe("done"));
    expect(client.getAnalysis).toHaveBeenCalledTimes(3);
  });

  it("fails fast when polling is refused with a 4xx", async () => {
    const client = fakeClient();
    client.getAnalysis.mockRejectedValue(new ApiError(404, "analysis not found"));
    const { result } = renderAnalysis(client);

    act(() => result.current.submit(PAIR));

    await waitFor(() => expect(result.current.state.phase).toBe("failed"));
    expect(client.getAnalysis).toHaveBeenCalledTimes(1);
  });

  it("abandons an upload on reset and ignores its late answer", async () => {
    const first = deferred<{ id: string }>();
    const second = deferred<{ id: string }>();
    const client = fakeClient();
    client.createAnalysis.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    client.streamAnalysisEvents.mockReturnValue(new Promise<StreamOutcome>(() => {}));
    const { result } = renderAnalysis(client);

    act(() => result.current.submit(PAIR));
    act(() => result.current.reset());
    expect(result.current.state.phase).toBe("idle");
    act(() => result.current.submit(PAIR));
    await act(async () => first.resolve({ id: "stale-job" }));

    expect(result.current.state.phase).toBe("uploading");
    await act(async () => second.resolve({ id: JOB_ID }));
    expect(result.current.state).toMatchObject({ phase: "running", jobId: JOB_ID });
    expect(client.streamAnalysisEvents).toHaveBeenCalledTimes(1);
    expect(client.streamAnalysisEvents.mock.calls[0]?.[0]).toBe(JOB_ID);
  });

  it("abandons a running analysis on reset: the stream is aborted and its end ignored", async () => {
    const outcome = deferred<StreamOutcome>();
    let options: StreamOptions | undefined;
    const client = fakeClient();
    client.streamAnalysisEvents.mockImplementation((_id, given) => {
      options = given;
      return outcome.promise;
    });
    const { result } = renderAnalysis(client);

    act(() => result.current.submit(PAIR));
    await waitFor(() => expect(result.current.state.phase).toBe("running"));
    act(() => result.current.reset());
    await act(async () => {
      options?.onEvent(finished(1, "done"));
      outcome.resolve("finished");
    });

    expect(options?.signal?.aborted).toBe(true);
    expect(result.current.state.phase).toBe("idle");
    expect(client.getAnalysis).not.toHaveBeenCalled();
  });
});
