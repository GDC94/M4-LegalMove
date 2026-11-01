/**
 * Runtime guards for every body and event the API sends. Each schema `satisfies` the type
 * generated from `openapi.json`, so a backend change breaks `tsc` here instead of the screen.
 */
import { z } from "zod";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
type StreamedEvent =
  paths["/analyses/{job_id}/events"]["get"]["responses"][200]["content"]["text/event-stream"];

export const STAGE_NAMES = [
  "parse_original_contract",
  "parse_amendment_contract",
  "contextualization_agent",
  "extraction_agent",
  "output_validation",
] as const satisfies readonly Schemas["StageName"][];

export const stageNameSchema = z.enum(STAGE_NAMES) satisfies z.ZodType<Schemas["StageName"]>;

export const stageStatusSchema = z.enum(["started", "done", "failed"]) satisfies z.ZodType<
  Schemas["StageStatus"]
>;

export const jobStateSchema = z.enum([
  "pending",
  "running",
  "done",
  "human_review",
  "failed",
]) satisfies z.ZodType<Schemas["JobState"]>;

export const stageEventSchema = z.object({
  seq: z.number().int(),
  kind: z.literal("stage"),
  stage: stageNameSchema,
  status: stageStatusSchema,
  at: z.string(),
  // Only `true` or absent: the API omits the field on every Stage the cache did not serve.
  cached: z.literal(true).optional(),
}) satisfies z.ZodType<Schemas["StageEvent"]>;

export const jobFinishedEventSchema = z.object({
  seq: z.number().int(),
  kind: z.literal("job"),
  status: z.enum(["done", "human_review", "failed"]),
  at: z.string(),
}) satisfies z.ZodType<Schemas["JobFinishedEvent"]>;

export const progressEventSchema = z.discriminatedUnion("kind", [
  stageEventSchema,
  jobFinishedEventSchema,
]) satisfies z.ZodType<StreamedEvent>;

export const validationIssueSchema = z.object({
  code: z.string(),
  path: z.string(),
  message: z.string(),
}) satisfies z.ZodType<Schemas["ValidationIssue"]>;

export const errorEnvelopeSchema = z.object({
  error: z.string(),
  stage: z.string(),
  message: z.string(),
  run_id: z.string(),
  trace_id: z.string().nullable(),
  issues: z.array(validationIssueSchema).nullish(),
}) satisfies z.ZodType<Schemas["ErrorEnvelope"]>;

export const contractChangeOutputSchema = z.object({
  sections_changed: z.array(z.string()),
  topics_touched: z.array(z.string()),
  summary_of_the_change: z.string(),
}) satisfies z.ZodType<Schemas["ContractChangeOutput"]>;

export const changeTypeSchema = z.enum(["added", "removed", "modified"]) satisfies z.ZodType<
  Schemas["ChangeType"]
>;

export const topicSchema = z.enum([
  "parties",
  "object_scope",
  "price_payment",
  "term_duration",
  "termination",
  "obligations",
  "deliverables_sla",
  "penalties",
  "liability_indemnity",
  "warranties",
  "confidentiality",
  "intellectual_property",
  "data_protection",
  "insurance",
  "assignment",
  "governing_law_disputes",
  "notices",
  "other",
]) satisfies z.ZodType<Schemas["Topic"]>;

export const amendmentKindSchema = z.enum(["restated", "delta", "unknown"]) satisfies z.ZodType<
  Schemas["AmendmentKind"]
>;

// The quotes are the contract's literal text, unmasked: the owner of the document asked for them.
export const changeDetailSchema = z.object({
  section_id: z.string(),
  section_title: z.string().nullable(),
  change_type: changeTypeSchema,
  topic: topicSchema,
  description: z.string(),
  original_quote: z.string().nullable(),
  amendment_quote: z.string().nullable(),
}) satisfies z.ZodType<Schemas["ChangeDetail"]>;

export const analysisBodySchema = z.object({
  id: z.string(),
  status: jobStateSchema,
  created_at: z.string(),
  trace_id: z.string().nullable(),
  output: contractChangeOutputSchema.nullish(),
  changes: z.array(changeDetailSchema).nullish(),
  amendment_kind: amendmentKindSchema.nullish(),
  error: errorEnvelopeSchema.nullish(),
}) satisfies z.ZodType<Schemas["AnalysisBody"]>;

export const analysisCreatedSchema = z.object({ id: z.string() }) satisfies z.ZodType<
  Schemas["AnalysisCreated"]
>;

export const apiProblemSchema = z.object({ detail: z.string() }) satisfies z.ZodType<
  Schemas["ApiProblem"]
>;

export type StageName = z.output<typeof stageNameSchema>;
export type ProgressEvent = z.output<typeof progressEventSchema>;
export type AnalysisBody = z.output<typeof analysisBodySchema>;
export type ErrorEnvelope = z.output<typeof errorEnvelopeSchema>;
export type ContractChangeOutput = z.output<typeof contractChangeOutputSchema>;
export type ChangeDetail = z.output<typeof changeDetailSchema>;
export type AmendmentKind = z.output<typeof amendmentKindSchema>;
