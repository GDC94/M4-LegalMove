import { type FormEvent, useId, useState } from "react";
import { ACCEPTED_TYPES, MAX_IMAGE_BYTES, uploadFormSchema } from "@/analysis/upload-form-schema";
import type { ImagePair } from "@/api/client";
import {
  type AttachmentRejectReason,
  AttachmentUpload,
  type AttachmentUploadItem,
} from "@/components/motion/attachment-upload";

type Role = keyof ImagePair;
type PerRole<T> = Partial<Record<Role, T>>;

const ROLES: { role: Role; legend: string; hint: string }[] = [
  { role: "original", legend: "Original contract", hint: "The signed contract, one page" },
  { role: "amendment", legend: "Amendment", hint: "The amendment to compare, one page" },
];

/** Two uploads fix each image's role by position; the schema checks the pair before sending. */
export function UploadForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (pair: ImagePair) => void;
}) {
  const [items, setItems] = useState<Record<Role, AttachmentUploadItem[]>>({
    original: [],
    amendment: [],
  });
  const [problems, setProblems] = useState<PerRole<string>>({});
  const [rejections, setRejections] = useState<PerRole<string>>({});
  const formId = useId();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = uploadFormSchema.safeParse({
      original: items.original[0]?.file,
      amendment: items.amendment[0]?.file,
    });
    if (!parsed.success) {
      setProblems(
        Object.fromEntries(parsed.error.issues.map((issue) => [issue.path[0], issue.message])),
      );
      return;
    }
    setProblems({});
    onSubmit(parsed.data);
  }

  function select(role: Role, next: AttachmentUploadItem[]) {
    setItems((current) => ({ ...current, [role]: next }));
    setRejections((current) => ({ ...current, [role]: undefined }));
    setProblems((current) => ({ ...current, [role]: undefined }));
  }

  function reject(role: Role, files: File[], reason: AttachmentRejectReason) {
    const name = files[0]?.name ?? "The image";
    const message = reason === "too-large" ? `${name} is over 10 MB.` : "Only one image fits here.";
    setRejections((current) => ({ ...current, [role]: message }));
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-6">
      <div className="grid gap-6 md:grid-cols-2">
        {ROLES.map(({ role, legend, hint }) => {
          const messages = [rejections[role], problems[role]].filter(
            (message) => message !== undefined,
          );
          const messagesId = `${formId}-${role}-messages`;
          return (
            <fieldset
              key={role}
              disabled={busy}
              aria-describedby={messages.length > 0 ? messagesId : undefined}
              className="min-w-0 space-y-2"
            >
              <legend className="mb-2 text-sm font-semibold text-foreground">{legend}</legend>
              <AttachmentUpload
                value={items[role]}
                onValueChange={(next) => select(role, next)}
                onFilesRejected={(files, reason) => reject(role, files, reason)}
                accept={ACCEPTED_TYPES.join(",")}
                multiple={false}
                maxFiles={1}
                maxFileSize={MAX_IMAGE_BYTES}
                disabled={busy}
                title="Drop a PNG or JPEG, or browse"
                description={hint}
                attachmentsLabel={`${legend} image`}
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
          );
        })}
      </div>
      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-60 md:w-auto"
      >
        {busy ? "Uploading…" : "Analyze"}
      </button>
    </form>
  );
}
