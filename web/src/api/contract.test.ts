import { describe, expect, expectTypeOf, it } from "vitest";
import type { z } from "zod";
import {
  analysisBodySchema,
  type analysisCreatedSchema,
  type apiProblemSchema,
  type errorEnvelopeSchema,
  progressEventSchema,
} from "./contract";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
type StreamedEvent =
  paths["/analyses/{job_id}/events"]["get"]["responses"][200]["content"]["text/event-stream"];

const DONE_BODY = {
  id: "abc",
  status: "done",
  created_at: "2026-10-29T18:03:00.000Z",
  trace_id: "trace-1",
  output: {
    sections_changed: ["3"],
    topics_touched: ["price"],
    summary_of_the_change: "The price goes from 100 to 120.",
  },
};

describe("contract schemas", () => {
  // `satisfies` in contract.ts only proves the schema fits the type; these prove the reverse too,
  // so a field the backend adds or drops breaks `tsc` here.
  it("infer exactly the types generated from the OpenAPI", () => {
    expectTypeOf<z.output<typeof analysisBodySchema>>().toEqualTypeOf<Schemas["AnalysisBody"]>();
    expectTypeOf<z.output<typeof errorEnvelopeSchema>>().toEqualTypeOf<Schemas["ErrorEnvelope"]>();
    expectTypeOf<z.output<typeof analysisCreatedSchema>>().toEqualTypeOf<
      Schemas["AnalysisCreated"]
    >();
    expectTypeOf<z.output<typeof apiProblemSchema>>().toEqualTypeOf<Schemas["ApiProblem"]>();
    expectTypeOf<z.output<typeof progressEventSchema>>().toEqualTypeOf<StreamedEvent>();
  });

  it("accept a done body and a human review body with issues", () => {
    expect(analysisBodySchema.parse(DONE_BODY).output?.sections_changed).toEqual(["3"]);
    const review = analysisBodySchema.parse({
      ...DONE_BODY,
      status: "human_review",
      output: undefined,
      error: {
        error: "InvalidExtractionError",
        stage: "extraction",
        message: "extraction is still invalid after one retry",
        run_id: "abc",
        trace_id: null,
        issues: [{ code: "ungrounded_quote", path: "changes[0]", message: "m" }],
      },
    });
    expect(review.error?.issues?.[0]?.code).toBe("ungrounded_quote");
  });

  it("reject a body with an unknown status or a missing field", () => {
    expect(analysisBodySchema.safeParse({ ...DONE_BODY, status: "cancelled" }).success).toBe(false);
    expect(analysisBodySchema.safeParse({ ...DONE_BODY, trace_id: undefined }).success).toBe(false);
  });

  it("tell the two event kinds apart and reject a stage outside StageName", () => {
    const stage = progressEventSchema.parse({
      seq: 1,
      kind: "stage",
      stage: "extraction_agent",
      status: "started",
      at: "2026-10-29T18:03:11.412Z",
    });
    const job = progressEventSchema.parse({
      seq: 2,
      kind: "job",
      status: "human_review",
      at: "2026-10-29T18:03:40.009Z",
    });
    expect(stage.kind === "stage" && stage.stage).toBe("extraction_agent");
    expect(job.kind).toBe("job");
    expect(
      progressEventSchema.safeParse({
        seq: 3,
        kind: "stage",
        stage: "ocr",
        status: "done",
        at: "x",
      }).success,
    ).toBe(false);
  });
});
