import { Check } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import {
  ACCEPTED_TYPES,
  imageFile,
  MAX_IMAGE_BYTES,
  uploadFormSchema,
} from "@/analysis/upload-form-schema";
import type { ImagePair } from "@/api/client";
import {
  type AttachmentRejectReason,
  AttachmentUpload,
  type AttachmentUploadItem,
} from "@/components/motion/attachment-upload";
import { cn } from "@/lib/utils";

type Role = keyof ImagePair;
type Step = Role;

const STEPS: Record<
  Step,
  { number: number; label: string; title: string; hint: string; announcement: string }
> = {
  original: {
    number: 1,
    label: "Original contract",
    title: "Upload the original contract",
    hint: "The signed contract, one page",
    announcement: "Step 1 of 2: original contract",
  },
  amendment: {
    number: 2,
    label: "Amendment",
    title: "Upload the amendment",
    hint: "The amendment to compare, one page",
    announcement: "Step 2 of 2: amendment",
  },
};
const STEP_ORDER: Step[] = ["original", "amendment"];
const MISSING: Record<Role, string> = {
  original: "Choose the original contract image.",
  amendment: "Choose the amendment image.",
};

/**
 * Two steps, one upload each: the original first, then the amendment. Each file is checked as it
 * arrives, so a wrong file never moves the form on; the pair is checked again on submit.
 * `onAnnounce` feeds the screen's live region, which lives outside the animated form.
 */
export function UploadForm({
  busy,
  onSubmit,
  onAnnounce,
}: {
  busy: boolean;
  onSubmit: (pair: ImagePair) => void;
  onAnnounce: (message: string) => void;
}) {
  const [step, setStep] = useState<Step>("original");
  const [original, setOriginal] = useState<File>();
  const [amendment, setAmendment] = useState<AttachmentUploadItem[]>([]);
  const [problem, setProblem] = useState<string>();
  const [rejection, setRejection] = useState<string>();
  const heading = useRef<HTMLHeadingElement>(null);
  const focusOnStep = useRef(false);
  const formId = useId();

  // The form is mounted fresh for every upload: clear what a previous run announced.
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs once, on mount
  useEffect(() => onAnnounce(""), []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the step change is the trigger
  useEffect(() => {
    if (!focusOnStep.current) return;
    focusOnStep.current = false;
    // Deferred: a native file picker hands the focus back to its input right after `change`.
    const timer = setTimeout(() => heading.current?.focus(), 0);
    return () => clearTimeout(timer);
  }, [step]);

  function goTo(next: Step) {
    focusOnStep.current = true;
    setStep(next);
    setProblem(undefined);
    setRejection(undefined);
    onAnnounce(STEPS[next].announcement);
  }

  function chooseOriginal(next: AttachmentUploadItem[]) {
    const file = next[0]?.file;
    if (!file) return;
    if (!accept("original", file)) return;
    setOriginal(file);
    goTo("amendment");
  }

  function chooseAmendment(next: AttachmentUploadItem[]) {
    const item = next[0];
    if (item?.file && !accept("amendment", item.file)) return;
    setAmendment(next);
    setProblem(undefined);
    setRejection(undefined);
  }

  /** The schema's own message, and the file is dropped: the form stays where it was. */
  function accept(role: Role, file: File): boolean {
    const result = imageFile(MISSING[role]).safeParse(file);
    setRejection(undefined);
    setProblem(result.success ? undefined : result.error.issues[0]?.message);
    return result.success;
  }

  function reject(files: File[], reason: AttachmentRejectReason) {
    const name = files[0]?.name ?? "The image";
    setProblem(undefined);
    setRejection(reason === "too-large" ? `${name} is over 10 MB.` : "Only one image fits here.");
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = uploadFormSchema.safeParse({ original, amendment: amendment[0]?.file });
    if (!parsed.success) {
      setProblem(parsed.error.issues[0]?.message);
      return;
    }
    setProblem(undefined);
    onSubmit(parsed.data);
  }

  const current = STEPS[step];
  const messages = [rejection, problem].filter((message) => message !== undefined);
  const messagesId = `${formId}-messages`;
  const ready = original !== undefined && amendment[0]?.file !== undefined;

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-6">
      <Steps current={step} originalChosen={original !== undefined} />
      <div className="space-y-4">
        <h2
          ref={heading}
          tabIndex={-1}
          className="text-sm font-semibold text-foreground focus:outline-none"
        >
          {current.title}
        </h2>
        {step === "amendment" && original && (
          <FileRow
            file={original}
            action={
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setOriginal(undefined);
                  goTo("original");
                }}
                className="rounded-full border border-border-strong px-3 py-1 text-xs font-semibold text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              >
                Change
              </button>
            }
          />
        )}
        <fieldset
          disabled={busy}
          aria-describedby={messages.length > 0 ? messagesId : undefined}
          className="min-w-0 space-y-2"
        >
          <legend className="mb-2 text-sm font-semibold text-foreground">{current.label}</legend>
          <AttachmentUpload
            // Step 1 never holds a file: a valid one moves the form on, an invalid one is dropped.
            value={step === "original" ? [] : amendment}
            onValueChange={step === "original" ? chooseOriginal : chooseAmendment}
            onFilesRejected={reject}
            accept={ACCEPTED_TYPES.join(",")}
            multiple={false}
            maxFiles={1}
            maxFileSize={MAX_IMAGE_BYTES}
            disabled={busy}
            title="Drop a PNG or JPEG, or browse"
            description={current.hint}
            attachmentsLabel={`${current.label} image`}
            describedBy={messages.length > 0 ? messagesId : undefined}
          />
          <div id={messagesId}>
            {messages.map((message) => (
              <p key={message} role="alert" className="text-sm text-destructive">
                {message}
              </p>
            ))}
          </div>
        </fieldset>
        {step === "original" && amendment[0]?.file && (
          // Read-only: the amendment chosen earlier survives a change of the original.
          <FileRow file={amendment[0].file} caption="Amendment" />
        )}
      </div>
      <button
        type="submit"
        disabled={busy || !ready}
        className="w-full rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-60 md:w-auto"
      >
        {busy ? "Uploading…" : "Analyze"}
      </button>
    </form>
  );
}

function Steps({ current, originalChosen }: { current: Step; originalChosen: boolean }) {
  return (
    <ol className="grid gap-2 sm:grid-cols-2">
      {STEP_ORDER.map((step) => {
        const { number, label } = STEPS[step];
        const done = step === "original" && originalChosen && current !== "original";
        return (
          <li
            key={step}
            aria-current={step === current ? "step" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm",
              step !== current && "text-muted-foreground",
            )}
          >
            {done ? (
              <Check aria-hidden="true" className="size-4 shrink-0 text-success" />
            ) : (
              <span aria-hidden="true" className="w-4 shrink-0 text-center font-mono text-xs">
                {number}
              </span>
            )}
            <span className="flex-1 font-medium">{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

function FileRow({
  file,
  caption,
  action,
}: {
  file: File;
  caption?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm">
      <div className="min-w-0 flex-1">
        {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
        <p className="truncate font-medium text-foreground">{file.name}</p>
      </div>
      <span className="text-xs text-muted-foreground">{formatSize(file.size)}</span>
      {action}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
