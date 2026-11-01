import type { AmendmentKind, ChangeDetail, ContractChangeOutput } from "@/api/contract";
import { ChangeDiffList } from "./change-diff-list";
import { StartOverButton } from "./start-over-button";

/** The `done` body: which sections changed, the topics they touch and the summary. */
export function ResultView({
  output,
  changes,
  amendmentKind,
  onReset,
}: {
  output: ContractChangeOutput;
  changes: ChangeDetail[];
  amendmentKind: AmendmentKind | null;
  onReset: () => void;
}) {
  return (
    <section
      aria-labelledby="result-title"
      className="space-y-5 rounded-3xl border border-border bg-card p-6"
    >
      <div className="flex items-center gap-3">
        <h2 id="result-title" className="text-lg font-semibold text-foreground">
          Legal changes
        </h2>
        {amendmentKind !== null && (
          <span className="rounded-full bg-background px-3 py-1 font-mono text-xs text-muted-foreground ring-1 ring-border">
            {amendmentKind}
          </span>
        )}
      </div>
      <TagList
        title="Sections changed"
        values={output.sections_changed}
        empty="No section changed."
      />
      <TagList title="Topics touched" values={output.topics_touched} empty="No topic touched." />
      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-foreground">Summary of the change</h3>
        <p className="text-sm leading-6 text-foreground">{output.summary_of_the_change}</p>
      </div>
      {changes.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-foreground">Change details</h3>
          <ChangeDiffList changes={changes} amendmentKind={amendmentKind} />
        </div>
      )}
      <StartOverButton label="Analyze another pair" onReset={onReset} />
    </section>
  );
}

function TagList({ title, values, empty }: { title: string; values: string[]; empty: string }) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {values.length === 0 ? (
        <p className="text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {values.map((value) => (
            <li
              key={value}
              className="rounded-full bg-background px-3 py-1 font-mono text-xs text-foreground ring-1 ring-border"
            >
              {value}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
