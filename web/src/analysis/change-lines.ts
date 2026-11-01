/** Turns one change into the typed lines its diff card prints. Pure: no React, no DOM. */
import { diffWords } from "diff";
import type { AmendmentKind, ChangeDetail } from "@/api/contract";

/** A run of text; `changed` marks the words that differ from the other quote. */
export type Fragment = { text: string; changed: boolean };

export type DiffLine =
  | { kind: "removed"; fragments: Fragment[] }
  | { kind: "added"; fragments: Fragment[] }
  | { kind: "instruction"; text: string };

export function changeLines(detail: ChangeDetail, kind: AmendmentKind | null): DiffLine[] {
  const { change_type: type, original_quote: original, amendment_quote: amendment } = detail;
  if (type === "modified" && original !== null && amendment !== null) {
    return wordDiff(original, amendment);
  }
  const lines: DiffLine[] = [];
  if (type !== "added" && original !== null) lines.push(plainLine("removed", original));
  if (type === "added" && amendment !== null) lines.push(plainLine("added", amendment));
  // In a delta, a removal's amendment quote is the repeal instruction, not new contract text.
  if (type === "removed" && kind === "delta" && amendment !== null) {
    lines.push({ kind: "instruction", text: amendment });
  }
  return lines;
}

function plainLine(kind: "removed" | "added", text: string): DiffLine {
  return { kind, fragments: [{ text, changed: false }] };
}

/** `diffWords` ignores whitespace-only differences, which are OCR noise between the two scans. */
function wordDiff(original: string, amendment: string): DiffLine[] {
  const parts = diffWords(original, amendment);
  return [
    { kind: "removed", fragments: fragmentsOf(parts, "removed") },
    { kind: "added", fragments: fragmentsOf(parts, "added") },
  ];
}

function fragmentsOf(parts: ReturnType<typeof diffWords>, side: "removed" | "added"): Fragment[] {
  const other = side === "removed" ? "added" : "removed";
  return parts
    .filter((part) => !part[other])
    .map((part) => ({ text: part.value, changed: Boolean(part[side]) }));
}
