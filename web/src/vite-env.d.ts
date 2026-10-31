/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DEMO?: string;
  readonly VITE_API_URL?: string;
  readonly VITE_DEV_FIXTURE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
