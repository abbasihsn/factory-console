// Relocates the Playwright-captured README screenshots into `docs/screenshots/`
// so they can be committed and rendered on GitHub. Reads every `*.png` the
// screenshots e2e wrote to `frontend/tests/e2e/__screenshots__/` (gitignored)
// and copies each to the repo-root `docs/screenshots/`, creating that dir if
// absent. Idempotent: it overwrites, so a re-run (even with no fresh captures)
// is safe.
// Run after `playwright test --grep screenshots` — wired as `pnpm screenshots`.
import { copyFileSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// All paths resolve from this script's own location (frontend/scripts) so it
// works regardless of the cwd it is invoked from: repo root is two levels up.
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const SOURCE_DIR = join(SCRIPT_DIR, '..', 'tests', 'e2e', '__screenshots__');
const TARGET_DIR = join(SCRIPT_DIR, '..', '..', 'docs', 'screenshots');

function main() {
	let pngs;
	try {
		pngs = readdirSync(SOURCE_DIR).filter((name) => name.endsWith('.png'));
	} catch {
		console.error(
			`copy-screenshots: ${SOURCE_DIR} not found — run the screenshots e2e first ` +
				'(`pnpm --dir frontend e2e --grep screenshots`).'
		);
		process.exit(1);
		return;
	}

	if (pngs.length === 0) {
		console.error(
			`copy-screenshots: no *.png in ${SOURCE_DIR} — run the screenshots e2e first ` +
				'(`pnpm --dir frontend e2e --grep screenshots`).'
		);
		process.exit(1);
		return;
	}

	mkdirSync(TARGET_DIR, { recursive: true });
	for (const name of pngs) {
		copyFileSync(join(SOURCE_DIR, name), join(TARGET_DIR, name));
		console.log(`copy-screenshots: copied ${name} -> docs/screenshots/${name}`);
	}
	console.log(`copy-screenshots: copied ${pngs.length} screenshot(s) to docs/screenshots/.`);
}

main();
