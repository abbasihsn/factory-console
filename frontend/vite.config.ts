import { sveltekit } from '@sveltejs/kit/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

// DERIVED from the same `PY_PORT` that `scripts/dev.sh` starts uvicorn on, not a
// second hardcoded copy of it. It used to be a literal `:8000` while dev.sh honoured
// the env var, so the natural reaction to a busy port — `PY_PORT=8001 make dev` —
// started the backend on 8001 and left the SPA proxying /api to 8000, i.e. to
// whatever else held it (often another factory-console). The result was a UI from
// this worktree silently talking to a DIFFERENT project's backend.
const API_PROXY_PORT = process.env.PY_PORT ?? '8000';
const API_PROXY_TARGET = `http://127.0.0.1:${API_PROXY_PORT}`;

export default defineConfig({
	plugins: [sveltekit(), svelteTesting()],
	server: {
		// Dev only: forward /api to the local backend. Production is same-origin,
		// so no proxy is used there.
		proxy: {
			'/api': {
				target: API_PROXY_TARGET,
				changeOrigin: true
			}
		}
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.{test,spec}.{js,ts}']
	}
});
