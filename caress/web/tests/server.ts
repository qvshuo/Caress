import app from "../index.html";
Bun.serve({ hostname: "127.0.0.1", port: 19308, routes: { "/": app } });
