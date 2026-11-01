import { setupServer } from "msw/node";

/** One MSW server for every test file; each test installs its own handlers with `server.use`. */
export const server = setupServer();
