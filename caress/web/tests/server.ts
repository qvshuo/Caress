import app from "../index.html";
import components from "./components.html";

Bun.serve({
  hostname: "127.0.0.1",
  port: 19308,
  routes: { "/": app, "/__components": components },
});
