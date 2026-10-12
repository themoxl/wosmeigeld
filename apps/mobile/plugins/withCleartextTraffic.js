// Erlaubt der Android-App unverschluesselte HTTP-Verbindungen.
//
// WARUM DAS NOETIG IST
// Seit Android 9 ist Klartext-HTTP fuer Apps mit targetSdkVersion 28 oder
// hoeher standardmaessig gesperrt. Der Serverbildschirm merkt davon nichts -
// "Finanzplaner Cloud" uebernimmt die eingebaute Adresse, ohne eine Anfrage zu
// stellen. Erst die Anmeldung laeuft dagegen und meldet nur
//   Network request failed
// ohne jeden Hinweis auf die Ursache.
//
// WARUM PAUSCHAL UND NICHT NUR FUER EINE ADRESSE
// Die App ist zum Selbsthosten gebaut: "Eigener Server" nimmt jede beliebige
// Adresse entgegen, im Regelfall eine LAN-Adresse ohne Zertifikat. Eine
// Ausnahmeliste mit festen Domaenen wuerde genau diesen Weg zunichtemachen.
// Die Oberflaeche weist stattdessen selbst darauf hin, sobald eine
// http-Adresse eingegeben wird ("Unverschluesselt (http) - nur im eigenen
// Netzwerk verwenden", siehe src/config/ServerSetupScreen.tsx).
//
// WEG DAVON
// Wer echtes HTTPS will, laesst sich von Tailscale ein Zertifikat fuer den
// ts.net-Namen ausstellen (`tailscale serve`) - das sind gueltige
// Let's-Encrypt-Zertifikate, kein Selbstsigniertes zum Durchwinken. Dann
// braucht es diese Erweiterung fuer die eigene Installation nicht mehr; fuer
// fremde Heimserver ohne Zertifikat bleibt sie sinnvoll.
//
// Der Umweg ueber require.resolve: unter pnpm liegt @expo/config-plugins nicht
// im node_modules dieses Projekts, sondern nur als Abhaengigkeit von expo.
// Ueber dessen Pfad ist es zuverlaessig erreichbar, ohne das Paket zusaetzlich
// als eigene Abhaengigkeit zu fuehren.
const { withAndroidManifest } = require(
  require.resolve("@expo/config-plugins", {
    paths: [require.resolve("expo/package.json")],
  }),
);

module.exports = function withCleartextTraffic(config) {
  return withAndroidManifest(config, (cfg) => {
    const application = cfg.modResults.manifest.application?.[0];
    if (!application) {
      throw new Error(
        "AndroidManifest ohne <application> - withCleartextTraffic kann nichts setzen.",
      );
    }
    application.$["android:usesCleartextTraffic"] = "true";
    return cfg;
  });
};
