import { addBreadcrumb, captureMessage, flush, init, setTag } from "../../sdk-js/src/index";

const params = new URLSearchParams(location.search);
const dsn = params.get("dsn") || (document.getElementById("dsn") as HTMLInputElement).value;
const release = params.get("release") || "demo-1.0.0";

function startSdk() {
  const d = (document.getElementById("dsn") as HTMLInputElement).value || dsn;
  init({ dsn: d, release, environment: "browser-demo" });
  setTag("page", "demo");
}

function renderCart(cart: { items: { price: number }[] } | undefined) {
  return cart!.items.map((i) => i.price).reduce((a, b) => a + b, 0); // TypeError when cart is undefined
}

function parseConfig(text: string) {
  return JSON.parse(text); // SyntaxError
}

const actions: Record<string, () => void> = {
  "type-error": () => renderCart(undefined),
  "syntax-error": () => parseConfig("{not json"),
  "rejection": () => { void Promise.reject(new Error("unhandled promise rejection")); },
  "message": () => { captureMessage("user clicked the message button", "info"); },
};

document.querySelectorAll<HTMLButtonElement>("button[data-action]").forEach((b) =>
  b.addEventListener("click", () => {
    startSdk();
    addBreadcrumb({ message: `clicked ${b.dataset.action}`, category: "ui.click" });
    setTimeout(() => { actions[b.dataset.action!](); void flush(3000).then((ok) => (document.getElementById("status")!.textContent = ok ? "sent" : "flush timed out")); }, 0);
  }),
);
