import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, authenticate } from "./api";

afterEach(() => vi.unstubAllGlobals());

const reply = (status: number, body: unknown) =>
  vi.fn().mockResolvedValue({ ok: status < 400, status, statusText: "x", json: async () => body });

describe("authenticate", () => {
  it("posts credentials to the chosen auth endpoint and returns the token", async () => {
    const f = reply(200, { token: "tok123" });
    vi.stubGlobal("fetch", f);
    await expect(authenticate("http://api.test/", "login", "a@b.co", "password1")).resolves.toBe("tok123");
    expect(f.mock.calls[0][0]).toBe("http://api.test/api/0/auth/login");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ email: "a@b.co", password: "password1" });
  });

  it("surfaces the server's error message", async () => {
    vi.stubGlobal("fetch", reply(401, { detail: "wrong email or password" }));
    await expect(authenticate("", "login", "a@b.co", "password1")).rejects.toMatchObject({ status: 401, message: "wrong email or password" });
  });

  it("joins validation errors from a 422", async () => {
    vi.stubGlobal("fetch", reply(422, { detail: [{ msg: "too short" }, { msg: "bad email" }] }));
    const e = await authenticate("", "register", "x", "y").catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.message).toBe("too short; bad email");
  });
});
