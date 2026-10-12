import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  clearStoredServerConfig,
  getStoredServerConfig,
  setStoredServerConfig,
  type ServerConfig,
} from "./serverConfig";

const SELF_HOSTED: ServerConfig = {
  mode: "self-hosted",
  apiBaseUrl: "http://192.168.1.23:8882",
  supabaseUrl: "https://nas.example/supabase",
  supabaseAnonKey: "anon-key-value",
  instanceDisplayName: "Meine NAS",
};

describe("serverConfig persistence", () => {
  afterEach(async () => {
    await AsyncStorage.clear();
  });

  it("liefert null, wenn noch nie eine Wahl gespeichert wurde", async () => {
    await expect(getStoredServerConfig()).resolves.toBeNull();
  });

  it("speichert und liest eine Konfiguration verlustfrei zurueck", async () => {
    await setStoredServerConfig(SELF_HOSTED);
    await expect(getStoredServerConfig()).resolves.toEqual(SELF_HOSTED);
  });

  it("clearStoredServerConfig setzt wieder auf null zurueck", async () => {
    await setStoredServerConfig(SELF_HOSTED);
    await clearStoredServerConfig();
    await expect(getStoredServerConfig()).resolves.toBeNull();
  });

  it("liefert null statt zu werfen, wenn der gespeicherte Wert kaputtes JSON ist", async () => {
    await AsyncStorage.setItem("finanzplaner.serverConfig", "{ nicht valides json");
    await expect(getStoredServerConfig()).resolves.toBeNull();
  });

  it("liefert null, wenn der gespeicherte Wert nicht die erwartete Form hat", async () => {
    await AsyncStorage.setItem("finanzplaner.serverConfig", JSON.stringify({ mode: "cloud" }));
    await expect(getStoredServerConfig()).resolves.toBeNull();
  });
});
