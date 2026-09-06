// Independent Node implementation of the canonical integer subset; no device access.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

function unicode(text) {
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(++i);
      assert.ok(next >= 0xdc00 && next <= 0xdfff, 'Unpaired high surrogate');
    } else {
      assert.ok(code < 0xdc00 || code > 0xdfff, 'Unpaired low surrogate');
    }
  }
  return JSON.stringify(text);
}

function canonical(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    assert.ok(Number.isSafeInteger(value) && !Object.is(value, -0), 'Nonportable number');
    return String(value);
  }
  if (typeof value === 'string') return unicode(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  assert.equal(typeof value, 'object');
  const members = Object.keys(value).sort().map(key => {
    assert.match(key, /^[\x20-\x7e]{1,128}$/);
    return `${unicode(key)}:${canonical(value[key])}`;
  });
  return `{${members.join(',')}}`;
}

const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex');
const vectors = JSON.parse(readFileSync(new URL('./vectors.json', import.meta.url), 'utf8'));
assert.equal(canonical(vectors.recipe.value), vectors.recipe.canonical_utf8);
assert.equal(sha256(canonical(vectors.recipe.value)), vectors.recipe.sha256);
for (const vector of vectors.valid_messages) {
  const wire = `${canonical(vector.value)}\n`;
  assert.equal(wire, vector.wire_utf8, vector.name);
  assert.equal(sha256(wire), vector.sha256, vector.name);
  assert.ok(Buffer.byteLength(wire, 'utf8') <= 8193, vector.name);
}
// These are serialization checks, not a replacement for the strict wire parser.
for (const invalid of [1.5, -0, Infinity, NaN, 9007199254740992, '\ud800', '\udc00']) {
  assert.throws(() => canonical(invalid));
}
assert.equal(canonical({ z: '𠮷\n', a: 9007199254740991 }), '{"a":9007199254740991,"z":"𠮷\\n"}');
console.log(JSON.stringify({ status: 'pass', canonical_messages: vectors.valid_messages.length, recipe_sha256: vectors.recipe.sha256 }));
