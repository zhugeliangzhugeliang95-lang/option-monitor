import assert from 'node:assert/strict';
import { test } from 'node:test';
import { api } from '../frontend/app/lib.ts';

function sequence(t, responses) {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    const result = responses[calls++];
    if (result instanceof Error) throw result;
    assert.ok(result, 'unexpected additional request');
    return result;
  });
  return () => calls;
}

test('read recovers after a proxy reset without surfacing the transient 500', async t => {
  const calls = sequence(t, [new Response('Internal Server Error', {status: 500}), Response.json({price: 123})]);
  assert.deepEqual(await api('/results/latest'), {price: 123});
  assert.equal(calls(), 2);
});

test('network reset on a read retries once', async t => {
  const calls = sequence(t, [new TypeError('Failed to fetch'), Response.json([])]);
  assert.deepEqual(await api('/monitors'), []);
  assert.equal(calls(), 2);
});

test('persistent outage remains visible and retries are bounded', async t => {
  const calls = sequence(t, [new Response('', {status: 503}), new Response('', {status: 503})]);
  await assert.rejects(api('/results/latest'), /HTTP 503/);
  assert.equal(calls(), 2);
});

test('bad parameters preserve the backend explanation without retry', async t => {
  const calls = sequence(t, [Response.json({detail: '未知取价口径'}, {status: 422})]);
  await assert.rejects(api('/statistics?mode=invalid'), /未知取价口径/);
  assert.equal(calls(), 1);
});

for (const method of ['POST', 'PATCH', 'DELETE']) {
  test(`${method} is never replayed after a failed response`, async t => {
    const calls = sequence(t, [new Response('Internal Server Error', {status: 500})]);
    await assert.rejects(api('/monitors/test', {value: 1}, method), /HTTP 500/);
    assert.equal(calls(), 1);
  });
}

test('write network errors do not retry either', async t => {
  const calls = sequence(t, [new TypeError('Failed to fetch')]);
  await assert.rejects(api('/manual-snapshots', {price: 123}), /Failed to fetch/);
  assert.equal(calls(), 1);
});
