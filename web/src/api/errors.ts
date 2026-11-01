/** The three ways a call to the API can fail, each with its own recovery on screen. */

/** A body or event that does not match the OpenAPI contract: never rendered half-way. */
export class ContractError extends Error {
  override name = "ContractError";
}

/** The API refused the request; `detail` is its one-sentence reason. */
export class ApiError extends Error {
  override name = "ApiError";

  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

/** The connection broke or the stream ended early; worth a retry. */
export class StreamInterruptedError extends Error {
  override name = "StreamInterruptedError";
}
