// Node 26 exposes optional global Web Storage accessors that are unavailable
// without CLI storage files and can shadow jsdom's implementation.
class MemoryStorage {
  #values = new Map()

  get length() {
    return this.#values.size
  }

  clear() {
    this.#values.clear()
  }

  getItem(key) {
    return this.#values.has(String(key)) ? this.#values.get(String(key)) : null
  }

  key(index) {
    return [...this.#values.keys()][index] ?? null
  }

  removeItem(key) {
    this.#values.delete(String(key))
  }

  setItem(key, value) {
    this.#values.set(String(key), String(value))
  }
}

for (const name of ['localStorage', 'sessionStorage']) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    value: new MemoryStorage(),
  })
}
