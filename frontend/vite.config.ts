import { sveltekit } from '@sveltejs/kit/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

const API_PROXY_TARGET = 'http://127.0.0.1:8000';

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
