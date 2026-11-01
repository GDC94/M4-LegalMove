/**
 * The upload form, checked before sending: UX help only. The backend's `413` and `422` are the
 * checks that count. Not part of the HTTP contract, so it lives in Zod alone.
 */
import { z } from "zod";

/** The API's default per-image limit (`MAX_IMAGE_BYTES`, also the cap of `API_MAX_UPLOAD_BYTES`). */
export const MAX_IMAGE_BYTES = 10_000_000;
export const ACCEPTED_TYPES = ["image/png", "image/jpeg"] as const;

function imageFile(missing: string) {
  return z
    .file({ error: (issue) => (issue.input === undefined ? missing : "Choose an image file.") })
    .mime([...ACCEPTED_TYPES], { error: "Choose a PNG or JPEG image." })
    .max(MAX_IMAGE_BYTES, { error: "The image must be at most 10 MB." });
}

export const uploadFormSchema = z.object({
  original: imageFile("Choose the original contract image."),
  amendment: imageFile("Choose the amendment image."),
});

export type UploadForm = z.output<typeof uploadFormSchema>;
