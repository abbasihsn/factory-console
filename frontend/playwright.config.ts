import { defineConfig, devices } from '@playwright/test';

// The e2e harness boots the real packaged `factory-console` on the
// `with_run_state` fixture (see tests/e2e/global-setup.ts), parses the served
// URL from its stdout, and points Playwright at it via FC_E2E_BASE_URL. The
// setup exports that env var AFTER the port is known; Playwright re-reads this
// config in each worker (spawned only after globalSetup resolves), so
// `use.baseURL` below picks up the value the workers inherit.
export default defineConfig({
	testDir: './tests/e2e',
	globalSetup: './tests/e2e/global-setup.ts',
	globalTeardown: './tests/e2e/global-teardown.ts',
	// One console, one port: run serially with a single worker so the shared
	// server isn't hit by parallel specs racing over the same fixture.
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: 'list',
	use: {
		baseURL: process.env.FC_E2E_BASE_URL,
		trace: 'on-first-retry'
	},
	// Only Chromium is installed in the harness environment.
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
