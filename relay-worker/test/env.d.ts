import type { D1Migration } from "@cloudflare/vitest-plugin";

declare global {
  namespace Cloudflare {
    interface Env {
      DB: D1Database;
      RELAY_ADMIN_TOKEN: string;
      SOUND_VAULT_RELAY_RATE_LIMIT: string;
      SOUND_VAULT_RELAY_RATE_WINDOW_SECONDS: string;
      TEST_MIGRATIONS: D1Migration[];
    }
  }
}
export {};
