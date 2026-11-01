/** There is no job retry: starting over means uploading the pair again. */
export function StartOverButton({
  label = "Start over",
  onReset,
}: {
  label?: string;
  onReset: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onReset}
      className="rounded-full border border-border-strong px-5 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      {label}
    </button>
  );
}
