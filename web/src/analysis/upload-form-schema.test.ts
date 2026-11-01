import { describe, expect, it } from "vitest";
import { pngFile } from "@/test/fixtures";
import { MAX_IMAGE_BYTES, uploadFormSchema } from "./upload-form-schema";

function messages(input: unknown): Record<string, string | undefined> {
  const result = uploadFormSchema.safeParse(input);
  if (result.success) return {};
  return Object.fromEntries(result.error.issues.map((issue) => [issue.path[0], issue.message]));
}

describe("uploadFormSchema", () => {
  it("accepts a PNG and a JPEG at the size limit", () => {
    const jpeg = new File([new Uint8Array(MAX_IMAGE_BYTES)], "amendment.jpg", {
      type: "image/jpeg",
    });

    expect(uploadFormSchema.safeParse({ original: pngFile(), amendment: jpeg }).success).toBe(true);
  });

  it("rejects a type that is not PNG or JPEG", () => {
    const gif = new File(["GIF89a"], "original.gif", { type: "image/gif" });

    expect(messages({ original: gif, amendment: pngFile() })).toEqual({
      original: "Choose a PNG or JPEG image.",
    });
  });

  it("rejects an image over the API limit", () => {
    const big = pngFile("amendment.png", MAX_IMAGE_BYTES + 1);

    expect(messages({ original: pngFile(), amendment: big })).toEqual({
      amendment: "The image must be at most 10 MB.",
    });
  });

  it("rejects an incomplete pair", () => {
    expect(messages({ original: pngFile() })).toEqual({ amendment: "Choose the amendment image." });
    expect(messages({ amendment: pngFile() })).toEqual({
      original: "Choose the original contract image.",
    });
  });
});
