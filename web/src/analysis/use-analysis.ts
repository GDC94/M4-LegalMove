/**
 * Drives one analysis through the API: upload, follow the event stream, then read the body.
 * The body is read the same way after a clean stream end and after the stream gave up, so
 * both paths settle on the same result; only the polling interval differs.
 */
import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ApiClient, ImagePair, StreamOutcome } from "@/api/client";
import type { AnalysisBody } from "@/api/contract";
import { ApiError, ContractError } from "@/api/errors";
import { type AnalysisEvent, INITIAL_STATE, transition } from "./machine";

const TERMINAL = new Set<AnalysisBody["status"]>(["done", "human_review", "failed"]);

type StreamEnd = { jobId: string; outcome: StreamOutcome };

/** Consecutive failed polls tolerated before the analysis is given up as failed. */
const MAX_POLL_RETRIES = 3;

export function useAnalysis(client: ApiClient, pollIntervalMs: number) {
  const [state, dispatch] = useReducer(transition, INITIAL_STATE);
  const [streamEnd, setStreamEnd] = useState<StreamEnd>();
  const queryClient = useQueryClient();
  // `busy` stops a second POST before the first re-render; `id` is bumped by every submit and
  // reset, so an upload that answers for an abandoned run is ignored.
  const run = useRef({ id: 0, busy: false });
  const jobId = state.phase === "running" ? state.jobId : undefined;

  useEffect(() => {
    if (jobId === undefined) return;
    const controller = new AbortController();
    client
      .streamAnalysisEvents(jobId, {
        onEvent: (event) => {
          if (!controller.signal.aborted) dispatch({ type: "progress", event });
        },
        signal: controller.signal,
      })
      .then((outcome) => {
        if (!controller.signal.aborted) setStreamEnd({ jobId, outcome });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) dispatch({ type: "error", message: describeError(error) });
      });
    return () => controller.abort();
  }, [client, jobId]);

  const body = useQuery({
    queryKey: ["analysis", jobId],
    queryFn:
      jobId !== undefined && streamEnd?.jobId === jobId
        ? () => client.getAnalysis(jobId)
        : skipToken,
    retry: (failures, error) => isTransient(error) && failures < MAX_POLL_RETRIES,
    retryDelay: (failures) => pollIntervalMs * 2 ** failures,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status !== undefined && TERMINAL.has(status) ? false : pollIntervalMs;
    },
  });

  useEffect(() => {
    if (body.data) dispatch({ type: "settled", body: body.data });
  }, [body.data]);

  useEffect(() => {
    if (body.error) dispatch({ type: "error", message: describeError(body.error) });
  }, [body.error]);

  const submit = useCallback(
    (pair: ImagePair) => {
      if (run.current.busy) return;
      const id = run.current.id + 1;
      run.current = { id, busy: true };
      dispatch({ type: "submit" });
      const ifCurrent = (event: AnalysisEvent) => {
        if (run.current.id === id) dispatch(event);
      };
      client.createAnalysis(pair).then(
        ({ id: created }) => ifCurrent({ type: "created", jobId: created }),
        (error: unknown) => ifCurrent({ type: "error", message: describeError(error) }),
      );
    },
    [client],
  );

  const reset = useCallback(() => {
    run.current = { id: run.current.id + 1, busy: false };
    setStreamEnd(undefined);
    queryClient.removeQueries({ queryKey: ["analysis"] });
    dispatch({ type: "reset" });
  }, [queryClient]);

  return { state, submit, reset };
}

/** A refusal (4xx) or a contract error will not change on retry; anything else might. */
function isTransient(error: unknown): boolean {
  if (error instanceof ContractError) return false;
  return !(error instanceof ApiError && error.status < 500);
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) return `The API refused the request: ${error.detail}`;
  if (error instanceof ContractError) {
    return "The API answered outside its contract, so nothing is shown rather than a partial result.";
  }
  return "The API could not be reached. Check that it is running and try again.";
}
