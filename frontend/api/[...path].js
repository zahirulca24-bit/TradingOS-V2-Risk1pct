import {
  createHmac,
  randomBytes,
  scryptSync,
  timingSafeEqual
} from "node:crypto";

const COOKIE = "tradingos_cloud_session";
const MAX_AGE = 8 * 60 * 60;
const RENDER_URL = "https://tradingos-v2-risk1pct.onrender.com";

function sessionSignature(expires, nonce, secret) {
  return createHmac("sha256", secret)
    .update(`${expires}.${nonce}`)
    .digest("base64url");
}

function readCookies(header = "") {
  return Object.fromEntries(
    header.split(";").map((entry) => {
      const index = entry.indexOf("=");
      if (index < 0) return ["", ""];
      return [
        entry.slice(0, index).trim(),
        entry.slice(index + 1).trim()
      ];
    })
  );
}

function authenticated(req, secret) {
  if (!secret) return false;

  const value = readCookies(req.headers.cookie)[COOKIE];
  if (!value) return false;

  const parts = value.split(".");
  if (parts.length !== 3) return false;

  const [expires, nonce, signature] = parts;
  if (!/^\d+$/.test(expires)) return false;
  if (Number(expires) <= Date.now()) return false;

  const expected = sessionSignature(expires, nonce, secret);
  const supplied = Buffer.from(signature);
  const correct = Buffer.from(expected);

  return (
    supplied.length === correct.length &&
    timingSafeEqual(supplied, correct)
  );
}

function cookie(value, maxAge) {
  return [
    `${COOKIE}=${value}`,
    "HttpOnly",
    "Secure",
    "SameSite=Lax",
    "Path=/",
    `Max-Age=${maxAge}`
  ].join("; ");
}

function json(res, status, body) {
  res.status(status).setHeader("Cache-Control", "no-store");
  return res.json(body);
}

export default async function handler(req, res) {
  const pathname = new URL(
    req.url || "/",
    "https://tradingos.invalid"
  ).pathname;

  const path = pathname.replace(/^\/api/, "");

  const secret = process.env.TRADINGOS_SESSION_SECRET;
  const storedPassword = process.env.TRADINGOS_LOGIN_SCRYPT;
  const renderToken = process.env.TRADINGOS_AUTH_TOKEN;

  if (req.method !== "GET" && req.method !== "HEAD") {
    const origin = req.headers.origin;
    const host = req.headers.host;

    if (!origin || new URL(origin).host !== host ||
        new URL(origin).protocol !== "https:") {
      return json(res, 403, { detail: "Invalid request origin" });
    }
  }

  if (path === "/auth/session" && req.method === "GET") {
    return json(res, 200, {
      authenticated: authenticated(req, secret)
    });
  }

  if (path === "/auth/logout" && req.method === "POST") {
    res.setHeader("Set-Cookie", cookie("", 0));
    return json(res, 200, { authenticated: false });
  }

  if (path === "/auth/login" && req.method === "POST") {
    if (!secret || !storedPassword || !renderToken) {
      return json(res, 503, {
        detail: "Cloud login is not configured"
      });
    }

    const password = req.body?.password;
    const parts = storedPassword.split(":");

    if (typeof password !== "string" ||
        password.length > 1024 ||
        parts.length !== 2) {
      return json(res, 401, { detail: "Invalid credentials" });
    }

    let valid = false;
    try {
      const actual = scryptSync(
        password,
        Buffer.from(parts[0], "hex"),
        64
      );
      const expected = Buffer.from(parts[1], "hex");

      valid = actual.length === expected.length &&
        timingSafeEqual(actual, expected);
    } catch {
      valid = false;
    }

    if (!valid) {
      return json(res, 401, { detail: "Invalid credentials" });
    }

    const expires = String(Date.now() + MAX_AGE * 1000);
    const nonce = randomBytes(16).toString("base64url");
    const signature = sessionSignature(expires, nonce, secret);

    res.setHeader(
      "Set-Cookie",
      cookie(`${expires}.${nonce}.${signature}`, MAX_AGE)
    );

    return json(res, 200, { authenticated: true });
  }

  if (req.method !== "GET") {
    return json(res, 405, { detail: "Method not allowed" });
  }

  const publicPath = path === "/health" ||
    path === "/" ||
    path.startsWith("/market/");

  if (!publicPath && !authenticated(req, secret)) {
    return json(res, 401, { detail: "Login required" });
  }

  if (!publicPath && !renderToken) {
    return json(res, 503, {
      detail: "Render authentication not configured"
    });
  }

  if (!/^\/[a-zA-Z0-9/_-]*$/.test(path)) {
    return json(res, 400, { detail: "Invalid API path" });
  }

  try {
    const originalUrl = new URL(
      req.url,
      "https://tradingos.invalid"
    );

    const upstream = new URL(path, RENDER_URL);
    upstream.search = originalUrl.search;

    const headers = {};
    if (!publicPath) {
      headers.Authorization = `Bearer ${renderToken}`;
    }

    const response = await fetch(upstream, {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(25000)
    });

    const body = await response.arrayBuffer();

    res.status(response.status);
    res.setHeader(
      "Content-Type",
      response.headers.get("content-type") ||
        "application/octet-stream"
    );
    res.setHeader("Cache-Control", "no-store");

    return res.send(Buffer.from(body));
  } catch {
    return json(res, 502, {
      detail: "Backend temporarily unavailable"
    });
  }
}
