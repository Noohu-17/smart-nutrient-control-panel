/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "false" turns the fake API off in development, so /api goes to the real back end. */
  readonly VITE_USE_MOCKS?: string;
  /** Where Vite's dev server sends /api when the fake API is off. Default http://localhost:8000. */
  readonly VITE_API_TARGET?: string;
  /** "true" builds the demo: role picker and "Demo · simulated farm" banner (T2-17). "false" turns it off in development. */
  readonly VITE_DEMO_MODE?: string;
}
