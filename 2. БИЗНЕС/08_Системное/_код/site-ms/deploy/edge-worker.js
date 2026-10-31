// Public edge for the existing site and /yuji/. The upstream is fixed;
// request URLs and user headers can never select another backend.
const ORIGIN = 'origin.ms-p.ru';

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target = new URL(request.url);
    target.protocol = 'https:';
    target.hostname = ORIGIN;
    target.port = '';
    const headers = new Headers(request.headers);
    headers.set('host', ORIGIN);
    headers.set('x-forwarded-host', incoming.host);
    headers.set('x-forwarded-proto', 'https');
    headers.delete('oai-sites-authorization');

    try {
      const upstream = await fetch(new Request(target, {
        method: request.method,
        headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
        duplex: 'half',
        redirect: 'manual',
      }), { cf: { cacheEverything: false, cacheTtl: 0 } });
      const responseHeaders = new Headers(upstream.headers);
      const location = responseHeaders.get('location');
      if (location) {
        const redirect = new URL(location, target);
        if (redirect.hostname === ORIGIN || redirect.hostname === 'ms-p.ru' || redirect.hostname === 'www.ms-p.ru') {
          redirect.protocol = incoming.protocol;
          redirect.host = incoming.host;
          responseHeaders.set('location', redirect.href);
        }
      }
      if (incoming.pathname === '/yuji' || incoming.pathname.startsWith('/yuji/')) {
        responseHeaders.set('cache-control', 'private, no-store');
      }
      return new Response(upstream.body, {
        status: upstream.status,
        statusText: upstream.statusText,
        headers: responseHeaders,
      });
    } catch (_) {
      return new Response('Сайт временно недоступен. Попробуйте обновить страницу.', {
        status: 502,
        headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' },
      });
    }
  },
};
