import { describe, expect, it } from "vitest";
import { ContractError } from "./errors";
import { readProgressEvents } from "./sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const frames = [];
  for await (const frame of readProgressEvents(stream)) frames.push(frame);
  return frames;
}

const STARTED =
  '{"seq":1,"kind":"stage","stage":"parse_original_contract","status":"started","at":"2026-10-29T18:03:11.412Z"}';
const FINISHED = '{"seq":2,"kind":"job","status":"done","at":"2026-10-29T18:03:40.009Z"}';

describe("readProgressEvents", () => {
  it("splits frames by blank lines, keeps each id and validates each data line", async () => {
    const frames = await collect(
      streamOf(`id: 1\ndata: ${STARTED}\n\nid: 2\ndata: ${FINISHED}\n\n`),
    );

    expect(frames).toEqual([
      {
        id: "1",
        event: {
          seq: 1,
          kind: "stage",
          stage: "parse_original_contract",
          status: "started",
          at: "2026-10-29T18:03:11.412Z",
        },
      },
      { id: "2", event: { seq: 2, kind: "job", status: "done", at: "2026-10-29T18:03:40.009Z" } },
    ]);
  });

  it("ignores keep-alive comments and reassembles frames cut across chunks", async () => {
    const whole = `: keep-alive\n\nid: 1\r\ndata: ${STARTED}\r\n\r\n: keep-alive\n\n`;
    const cuts = [5, 17, 40, 90];
    const chunks = cuts.map((end, index) => whole.slice(cuts[index - 1] ?? 0, end));
    chunks.push(whole.slice(cuts.at(-1)));

    const frames = await collect(streamOf(...chunks));

    expect(frames.map((frame) => frame.id)).toEqual(["1"]);
  });

  it("joins a CRLF pair cut between its CR and its LF", async () => {
    const whole = `id: 1\r\ndata: ${STARTED}\r\n\r\nid: 2\r\ndata: ${FINISHED}\r\n\r\n`;
    const cuts = [...whole.matchAll(/\r/g)].map((match) => (match.index ?? 0) + 1);
    const chunks = cuts.map((end, index) => whole.slice(cuts[index - 1] ?? 0, end));
    chunks.push(whole.slice(cuts.at(-1)));

    const frames = await collect(streamOf(...chunks));

    expect(chunks.every((chunk, index) => index === 0 || chunk.startsWith("\n"))).toBe(true);
    expect(frames.map((frame) => frame.id)).toEqual(["1", "2"]);
  });

  it("decodes a multi-byte UTF-8 character split across chunks", async () => {
    const event = {
      seq: 1,
      kind: "stage",
      stage: "parse_original_contract",
      status: "started",
      at: "Cláusula — 2026-10-29T18:03:11.412Z",
    };
    const bytes = new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(event)}\n\n`);
    const accent = bytes.indexOf(0xc3);
    const dash = bytes.indexOf(0xe2);
    const parts = [
      bytes.slice(0, accent + 1),
      bytes.slice(accent + 1, dash + 2),
      bytes.slice(dash + 2),
    ];
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const part of parts) controller.enqueue(part);
        controller.close();
      },
    });

    const frames = await collect(stream);

    expect(frames).toEqual([{ id: "1", event }]);
  });

  it("raises a contract error for data that does not pass the schema", async () => {
    const stray = '{"seq":3,"kind":"stage","stage":"ocr","status":"started","at":"x"}';

    await expect(collect(streamOf(`id: 3\ndata: ${stray}\n\n`))).rejects.toBeInstanceOf(
      ContractError,
    );
    await expect(collect(streamOf("id: 4\ndata: not json\n\n"))).rejects.toBeInstanceOf(
      ContractError,
    );
  });
});
