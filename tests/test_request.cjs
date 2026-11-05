'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {request, RequestError} = require('../community_booking/static/request.js');

test('GET returns JSON without a write body', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async (_path, options) => {
    assert.equal(options.method, 'GET');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.body, undefined);
    return Response.json({slots: []});
  });
  assert.deepEqual(await request('/api/availability'), {slots: []});
  assert.equal(fetch.mock.callCount(), 1);
});
test('409 preserves the actual conflict reason', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({detail: '请阅读当前协议'}, {status: 409}));
  await assert.rejects(request('/api/bookings', {}), error =>
    error instanceof RequestError && error.status === 409 && !error.uncertain && error.message === '请阅读当前协议');
});
test('a server write error is uncertain and never retried', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({detail: '暂不可用'}, {status: 503}));
  await assert.rejects(request('/api/bookings', {}), error => error.uncertain && /勿.*重复提交/.test(error.message));
  assert.equal(fetch.mock.callCount(), 1);
});
test('lost write response never silently retries or echoes the request body', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('network unavailable'); });
  await assert.rejects(request('/api/bookings', {management_secret: 'synthetic-secret'}), error =>
    error.uncertain && !error.message.includes('synthetic-secret'));
  assert.equal(fetch.mock.callCount(), 1);
});
test('unreadable successful write response is also uncertain', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('{', {headers: {'Content-Type': 'application/json'}}));
  await assert.rejects(request('/api/bookings', {}), error => error.uncertain);
});
test('read failure asks for another query without claiming a write', async t => {
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('offline'); });
  await assert.rejects(request('/api/config'), error => !error.uncertain && /重新查询/.test(error.message));
});
test('timeout aborts the in-flight request without retry', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', (_path, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
  }));
  await assert.rejects(request('/api/bookings', {}, {timeoutMs: 10}), error => error.uncertain);
  assert.equal(fetch.mock.callCount(), 1);
});
test('QR response is returned as a Blob', async t => {
  t.mock.method(globalThis, 'fetch', async () => new Response('<svg/>', {headers: {'Content-Type': 'image/svg+xml'}}));
  assert.equal(await (await request('/api/ticket/qr', {ticket: 'synthetic'})).text(), '<svg/>');
});
