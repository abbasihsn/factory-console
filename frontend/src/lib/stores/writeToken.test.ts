import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'factory-console:writeToken';

// A minimal in-memory `sessionStorage`: the store only calls getItem/setItem/
// removeItem, so the rest of the `Storage` surface is cast away at injection.
class FakeStorage {
	map = new Map<string, string>();

	getItem(key: string): string | null {
		return this.map.get(key) ?? null;
	}
	setItem(key: string, value: string): void {
		this.map.set(key, value);
	}
	removeItem(key: string): void {
		this.map.delete(key);
	}
}

// The store hydrates ONCE at import, so each case needs a fresh module instance
// against its own storage stub — hence resetModules + a dynamic import.
async function loadStore(storage: FakeStorage | undefined) {
	vi.resetModules();
	vi.stubGlobal('sessionStorage', storage as unknown as Storage | undefined);
	return await import('./writeToken');
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('writeToken store', () => {
	it('hydrates from sessionStorage at import', async () => {
		const storage = new FakeStorage();
		storage.setItem(STORAGE_KEY, 'tok-from-session');

		const { writeToken } = await loadStore(storage);

		expect(get(writeToken)).toBe('tok-from-session');
	});

	it('starts null when nothing is stored', async () => {
		const { writeToken } = await loadStore(new FakeStorage());

		expect(get(writeToken)).toBeNull();
	});

	it('mirrors setToken into sessionStorage, trimming the pasted value', async () => {
		const storage = new FakeStorage();
		const { writeToken, setToken } = await loadStore(storage);

		setToken('  tok-abc123\n');

		expect(get(writeToken)).toBe('tok-abc123');
		expect(storage.getItem(STORAGE_KEY)).toBe('tok-abc123');
	});

	it('removes the key on clearToken rather than storing "null"', async () => {
		const storage = new FakeStorage();
		storage.setItem(STORAGE_KEY, 'tok-abc123');
		const { writeToken, clearToken } = await loadStore(storage);

		clearToken();

		expect(get(writeToken)).toBeNull();
		expect(storage.map.has(STORAGE_KEY)).toBe(false);
	});

	it('treats a blank token as a clear, never storing an empty string', async () => {
		const storage = new FakeStorage();
		const { writeToken, setToken } = await loadStore(storage);

		setToken('tok-abc123');
		setToken('   ');

		expect(get(writeToken)).toBeNull();
		expect(storage.map.has(STORAGE_KEY)).toBe(false);
	});

	it('round-trips a token through a reload (a second import of the module)', async () => {
		const storage = new FakeStorage();
		const first = await loadStore(storage);
		first.setToken('tok-abc123');

		// A reload re-imports the module against the same (per-tab) storage.
		const reloaded = await loadStore(storage);

		expect(get(reloaded.writeToken)).toBe('tok-abc123');
	});

	it('works in memory with no window and no sessionStorage (SSR)', async () => {
		vi.stubGlobal('window', undefined);
		const { writeToken, setToken, clearToken } = await loadStore(undefined);

		// Hydration found no storage rather than throwing.
		expect(get(writeToken)).toBeNull();

		// Both mutators still drive the in-memory store and swallow the absent storage.
		expect(() => setToken('tok-abc123')).not.toThrow();
		expect(get(writeToken)).toBe('tok-abc123');
		expect(() => clearToken()).not.toThrow();
		expect(get(writeToken)).toBeNull();
	});

	it('survives a sessionStorage that throws on every operation', async () => {
		// Safari private mode / storage disabled by policy: the object exists but
		// throws. The token must still work for the life of the page.
		const hostile = {
			getItem() {
				throw new DOMException('SecurityError');
			},
			setItem() {
				throw new DOMException('QuotaExceededError');
			},
			removeItem() {
				throw new DOMException('SecurityError');
			}
		};
		vi.resetModules();
		vi.stubGlobal('sessionStorage', hostile as unknown as Storage);
		const { writeToken, setToken } = await import('./writeToken');

		expect(get(writeToken)).toBeNull();
		expect(() => setToken('tok-abc123')).not.toThrow();
		expect(get(writeToken)).toBe('tok-abc123');
	});
});
