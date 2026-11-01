/**
 * Server-sent events over `fetch` streaming. `EventSource` cannot send `X-API-Key`, so the
 * frames (`id:`, `data:`, `:` comments, blank-line separated) are parsed here.
 */
import { type ProgressEvent, progressEventSchema } from "./contract";
import { ContractError } from "./errors";

export type ProgressFrame = { id: string | undefined; event: ProgressEvent };

type RawFrame = { id: string | undefined; data: string[] };

export async function* readProgressEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<ProgressFrame> {
  for await (const frame of readFrames(body)) {
    if (frame.data.length > 0) yield { id: frame.id, event: parseEvent(frame.data.join("\n")) };
  }
}

async function* readFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<RawFrame> {
  const decoder = new TextDecoder();
  let buffer = "";
  let frame: RawFrame = { id: undefined, data: [] };
  for await (const chunk of readChunks(body)) {
    buffer += decoder.decode(chunk, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line === "") {
        yield frame;
        frame = { id: undefined, data: [] };
      } else {
        readField(frame, line);
      }
    }
  }
}

/** A reader loop rather than `for await` over the stream, which older Safari lacks. */
async function* readChunks(body: ReadableStream<Uint8Array>): AsyncGenerator<Uint8Array> {
  const reader = body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      yield value;
    }
  } finally {
    reader.releaseLock();
  }
}

function readField(frame: RawFrame, line: string): void {
  if (line.startsWith(":")) return;
  const colon = line.indexOf(":");
  const field = colon === -1 ? line : line.slice(0, colon);
  const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
  if (field === "id") frame.id = value;
  if (field === "data") frame.data.push(value);
}

function parseEvent(data: string): ProgressEvent {
  let json: unknown;
  try {
    json = JSON.parse(data);
  } catch {
    throw new ContractError("a progress event is not valid JSON");
  }
  const parsed = progressEventSchema.safeParse(json);
  if (!parsed.success) throw new ContractError("a progress event does not match the contract");
  return parsed.data;
}
