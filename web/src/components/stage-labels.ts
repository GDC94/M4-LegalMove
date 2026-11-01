import type { StageName } from "@/api/contract";

/** What each pipeline Stage does, in the words the screen uses. */
export const STAGE_LABELS: Record<StageName, string> = {
  parse_original_contract: "Read the original contract",
  parse_amendment_contract: "Read the amendment",
  contextualization_agent: "Map the sections",
  extraction_agent: "Extract the changes",
  output_validation: "Validate the result",
};
