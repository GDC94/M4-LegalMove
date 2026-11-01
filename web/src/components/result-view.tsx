import type { ContractChangeOutput } from "@/api/contract";
import { StartOverButton } from "./start-over-button";

/** The `done` body: which sections changed, the topics they touch and the summary. */
export function ResultView({
  output,
  onReset,
}: {
  output: ContractChangeOutput;
  onReset: () => void;
}) {
  return (
    <section
      aria-labelledby="result-title"
      className="space-y-5 rounded-3xl border border-border bg-card p-6"
    >
      <h2 id="result-title" className="text-lg font-semibold text-foreground">
        Legal changes
      </h2>
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
