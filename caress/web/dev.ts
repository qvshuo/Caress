import page from "./index.html";

const backend = process.env.CARESS_BACKEND ?? "http://caress:9307";
Bun.serve({
  hostname: "0.0.0.0",
  port: Number(process.env.PORT ?? 3000),
  routes: { "/": page },
  async fetch(request) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/"))
      return new Response("Not found", { status: 404 });
    const target = new URL(url.pathname + url.search, backend);
    return fetch(new Request(target, request));
  },
  development: { hmr: true, console: true },
});
