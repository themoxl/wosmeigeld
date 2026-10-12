import { discoverServerConfig } from "./discovery";

describe("discoverServerConfig", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("liefert Supabase-URL/Anon-Key/Anzeigename bei Erfolg", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        supabase_url: "https://nas.example/supabase",
        supabase_anon_key: "anon-key-value",
        instance_display_name: "Meine NAS",
      }),
    } as Response);

    const result = await discoverServerConfig("http://192.168.1.23:8882/");
    expect(result).toEqual({
      supabaseUrl: "https://nas.example/supabase",
      supabaseAnonKey: "anon-key-value",
      instanceDisplayName: "Meine NAS",
    });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://192.168.1.23:8882/v1/system/instance-config",
      expect.anything(),
    );
  });

  it("instance_display_name ist optional (null)", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        supabase_url: "https://nas.example/supabase",
        supabase_anon_key: "anon-key-value",
        instance_display_name: null,
      }),
    } as Response);

    const result = await discoverServerConfig("http://192.168.1.23:8882");
    expect(result.instanceDisplayName).toBeNull();
  });

  it("wirft bei Netzwerkfehler eine verstaendliche Meldung", async () => {
    globalThis.fetch = jest.fn().mockRejectedValue(new TypeError("Network request failed"));

    await expect(discoverServerConfig("http://unreachable.example")).rejects.toThrow(
      /nicht erreichbar/i,
    );
  });

  it("erkennt 503 (Instanz ohne Supabase-Konfiguration) gesondert", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ detail: "instance_not_configured" }),
    } as Response);

    await expect(discoverServerConfig("http://192.168.1.23:8882")).rejects.toThrow(
      /noch keine Supabase-Verbindung/i,
    );
  });

  it("wirft bei anderen HTTP-Fehlern mit Statuscode in der Meldung", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({}),
    } as Response);

    await expect(discoverServerConfig("http://192.168.1.23:8882")).rejects.toThrow(/404/);
  });

  it("wirft bei kaputtem JSON", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    } as unknown as Response);

    await expect(discoverServerConfig("http://192.168.1.23:8882")).rejects.toThrow(
      /kein gültiges JSON/i,
    );
  });

  it("wirft bei fehlenden Pflichtfeldern in der Antwort", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ supabase_url: "https://nas.example" }),
    } as Response);

    await expect(discoverServerConfig("http://192.168.1.23:8882")).rejects.toThrow(
      /erwartete Form/i,
    );
  });
});
