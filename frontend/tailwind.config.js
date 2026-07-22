/** @type {import('tailwindcss').Config} */
export default {
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			// Semantic colors backed by the design tokens in src/app.css, so
			// utilities like `bg-surface text-text text-muted text-danger` work.
			colors: {
				bg: 'var(--bg)',
				surface: 'var(--surface)',
				text: 'var(--text)',
				muted: 'var(--muted)',
				accent: 'var(--accent)',
				danger: 'var(--danger)'
			}
		}
	},
	plugins: []
};
