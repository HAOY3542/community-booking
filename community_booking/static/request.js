'use strict';
// Shared by the browser and Node's built-in regression runner. No automatic retries.
(() => {
  class RequestError extends Error {
    constructor(message, {status = 0, uncertain = false} = {}) {
      super(message);
      this.name = 'RequestError';
      this.status = status;
      this.uncertain = uncertain;
    }
  }
  async function request(path, body, {timeoutMs = 10000} = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const writing = body !== undefined;
    try {
      const response = await fetch(path, {
        method: writing ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        ...(writing ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})
      });
      if (!response.ok) {
        const value = await response.json().catch(() => ({}));
        throw new RequestError(typeof value.detail === 'string' ? value.detail : `请求失败 (${response.status})`,
          {status: response.status, uncertain: writing && response.status >= 500});
      }
      return await (response.headers.get('content-type')?.includes('image/') ? response.blob() : response.json());
    } catch (error) {
      if (error instanceof RequestError) {
        if (error.uncertain) error.message += '；结果待核对，请勿直接重复提交。';
        throw error;
      }
      throw new RequestError(writing
        ? '连接中断或响应无法读取；操作结果待核对，请勿直接重复提交。'
        : '读取未完成，请检查连接后重新查询。', {uncertain: writing});
    } finally {
      clearTimeout(timer);
    }
  }
  const api = {request, RequestError};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else globalThis.BookingHTTP = api;
})();
