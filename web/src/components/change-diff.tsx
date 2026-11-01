import { Check, ChevronDown, Copy } from "lucide-react";
import { useState } from "react";
import { changeLines, type DiffLine, type Fragment } from "@/analysis/change-lines";
import type { AmendmentKind, ChangeDetail } from "@/api/contract";
import { AgentDisclosure } from "@/components/motion/disclosure";
import { cn } from "@/lib/utils";

const COPIED_MS = 1500;

/** One change as a card: the section, what happened, and the quotes with the words that moved. */
export function ChangeDiff({
  detail,
  amendmentKind,
}: {
  detail: ChangeDetail;
  amendmentKind: AmendmentKind | null;
}) {
  const [open, setOpen] = useState(true);
  const lines = changeLines(detail, amendmentKind);
  const bodyId = `change-${detail.section_id}-${detail.change_type}-body`;

  return (
    <article className="space-y-3 rounded-2xl border border-border bg-background p-4">
      <header className="flex items-center gap-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={`Section ${detail.section_id} details`}
          onClick={() => setOpen((current) => !current)}
          className="rounded-full p-1 text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDown
            aria-hidden="true"
            className={cn("size-4 transition-transform", !open && "-rotate-90")}
          />
        </button>
        <span className="font-mono text-xs font-semibold text-foreground">{detail.section_id}</span>
        {detail.section_title !== null && (
          <span className="flex-1 truncate text-sm font-medium text-foreground">
            {detail.section_title}
          </span>
        )}
        <span className="ml-auto rounded-full bg-card px-2 py-0.5 text-xs text-muted-foreground ring-1 ring-border">
          {detail.change_type}
        </span>
        <CopyButton text={toPlainText(lines)} section={detail.section_id} />
      </header>
      <div className="space-y-1 pl-8">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          {detail.topic.replaceAll("_", " ")}
        </p>
        <p className="text-sm leading-6 text-foreground">{detail.description}</p>
      </div>
      <AgentDisclosure id={bodyId} open={open}>
        <div className="space-y-1 pt-1 font-mono text-xs">
          {lines.map((line, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: the lines of a card never reorder
            <Line key={`${line.kind}-${index}`} line={line} />
          ))}
        </div>
      </AgentDisclosure>
    </article>
  );
}

function Line({ line }: { line: DiffLine }) {
  if (line.kind === "instruction") {
    return (
      <div className="flex gap-3 rounded-lg px-2 py-1 text-muted-foreground italic">
        <span className="w-14 shrink-0 select-none">note</span>
        <p className="min-w-0 whitespace-pre-wrap break-words">
          <span className="font-semibold not-italic">Amendment instruction</span> {line.text}
        </p>
      </div>
    );
  }
  const removed = line.kind === "removed";
  return (
    <div
      className={cn(
        "flex gap-3 rounded-lg px-2 py-1",
        removed ? "bg-destructive/10" : "bg-success/15",
      )}
    >
      <span className="w-14 shrink-0 select-none text-muted-foreground">
        {removed ? "orig" : "amend"}
      </span>
      <span aria-hidden="true" className="select-none">
        {removed ? "−" : "+"}
      </span>
      <p className="min-w-0 whitespace-pre-wrap break-words text-foreground">
        <span className="sr-only">{removed ? "Removed: " : "Added: "}</span>
        {line.fragments.map((fragment, index) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: the fragments of a line never reorder
          <Words key={`${index}-${fragment.text}`} fragment={fragment} removed={removed} />
        ))}
      </p>
    </div>
  );
}

function Words({ fragment, removed }: { fragment: Fragment; removed: boolean }) {
  if (!fragment.changed) return <>{fragment.text}</>;
  return removed ? (
    <del className="rounded bg-destructive/25 no-underline">{fragment.text}</del>
  ) : (
    <ins className="rounded bg-success/30 no-underline">{fragment.text}</ins>
  );
}

function toPlainText(lines: DiffLine[]): string {
  return lines
    .map((line) => {
      if (line.kind === "instruction") return `Amendment instruction: ${line.text}`;
      const text = line.fragments.map((fragment) => fragment.text).join("");
      return `${line.kind === "removed" ? "-" : "+"} ${text}`;
    })
    .join("\n");
}

function CopyButton({ text, section }: { text: string; section: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard access can be denied or missing; the card still reads fine without it.
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), COPIED_MS);
  }

  const Icon = copied ? Check : Copy;
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? `Copied section ${section}` : `Copy section ${section}`}
      className="rounded-full p-1 text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Icon aria-hidden="true" className="size-4" />
    </button>
  );
}
