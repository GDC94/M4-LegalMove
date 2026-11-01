/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Sent as `X-API-Key`. Local demo only: anything here ships inside the bundle. */
  readonly VITE_API_KEY?: string;
  /** Where the Vite dev server proxies `/api`; read by `vite.config.ts`, not by the bundle. */
  readonly VITE_API_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// The vendored beUI tooltip reads `process.env.NODE_ENV`, which Vite replaces at build time;
// declaring it here avoids pulling Node's types into browser code.
declare const process: { env: { NODE_ENV?: string } };
