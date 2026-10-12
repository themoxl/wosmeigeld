/* Designkasten-Stand 84892444ba7c · Quelle: sia_public/designkasten · hier nicht aendern, sondern dort und dann node designkasten/verteilen.js */
/* Designkasten · designs.js — Liste der Designs, Merken, fruehes Anwenden.

   Gehoert in den <head>, VOR jedes Stylesheet-abhaengige Zeichnen, gefolgt von genau einem Aufruf:

     Designkasten.start({
       schluessel: "knimeup-design",            // localStorage-Schluessel des Programms (Pflicht)
       standard:   "klassisch",                 // gilt ohne gemerkte Wahl (Pflicht)
       eigene:     [{ id, name, text, art, probe, kopfFarbe? }],   // Designs, die nur dieses Programm hat
       auswahl:    ["warm", "ember", ...],      // Reihenfolge im Fenster; Dunkel und Hell je zusammenhaengend
       spiegeln:   function (id, design) {}     // setzt zusaetzlich das alte Merkmal des Programms
     });

   Gesetzt werden am <html>: data-design="<id>", data-design-art="dunkel|hell",
   data-design-herkunft="kasten|eigen", style.colorScheme und <meta name="theme-color">.
   Kasten-Designs bringen ihre Farben als --d-* aus designs.css mit; die Bruecke des Programms
   uebersetzt sie. Eigene Designs setzen mindestens die Fensterrollen (Designkasten.fensterrollen).

   Laut statt still: Falsche Aufrufe werfen. Eine gemerkte Wahl, die es nicht mehr gibt, und ein
   gesperrter Speicher fuehren zum Standard bzw. zu "nicht merken", stehen dann aber in
   Designkasten.meldung (das Fenster zeigt sie) und in der Konsole.

   Beim Wechsel: window-Ereignis "design-gewechselt" mit detail { id, art, herkunft }. */
(function (global) {
  "use strict";
  /* LISTE: von bauen.js aus SIA erzeugt - nicht von Hand aendern */
  var KASTEN = [
    {"id":"warm","name":"Warm","text":"Warmes Dunkel, wie SIA","art":"Dunkel","probe":{"kopf":"#2a201a","flaeche":"#1c1512","feld":"#2b211b","a":"#e0876d","b":"#c9913a"},"kopfFarbe":"#2a201a"},
    {"id":"ember","name":"Glut","text":"Warmes Dunkel, ruhigere Akzente","art":"Dunkel","probe":{"kopf":"#1f1613","flaeche":"#150f0d","feld":"#291d18","a":"#bd7263","b":"#c99c56"},"kopfFarbe":"#1e1613"},
    {"id":"dark","name":"Nacht","text":"Dunkles Grün","art":"Dunkel","probe":{"kopf":"#1e2a27","flaeche":"#17211f","feld":"#243330","a":"#8faa6f","b":"#e0b354"},"kopfFarbe":"#1e2927"},
    {"id":"daemmerung","name":"Dämmerung","text":"Violett bis Abendrot, mit Verlauf","art":"Dunkel","probe":{"kopf":"linear-gradient(90deg,#322b44,#3c2935 60%,#482322)","flaeche":"#17141c","feld":"#231e2b","a":"#ff9565","b":"#b89cff"},"kopfFarbe":"#322b44"},
    {"id":"oled","name":"OLED","text":"Echtes Schwarz, Lila-Akzente","art":"Dunkel","probe":{"kopf":"linear-gradient(90deg,#a78bfa,#ff9565) bottom/100% 1px no-repeat,#000","flaeche":"#000000","feld":"#16131c","a":"#a78bfa","b":"#ff9565"},"kopfFarbe":"#000000"},
    {"id":"light","name":"Wiese","text":"Hell, Papierton","art":"Hell","probe":{"kopf":"#f9f5ea","flaeche":"#f3eee1","feld":"#d9cfb8","a":"#5a7d4f","b":"#cf9a3a"},"kopfFarbe":"#faf6eb"}
  ];
  var ROLLEN = ["bg","panel","panel2","line","line-stark","fg","fg2","muted","faint","akzent","akzent2","akzent-hell","akzent-tief","auf-akzent","auf-akzent2","auf-akzent-tief","akzent-flaeche","hl","ok","warn","bad","info","auf-status","ok-stark","bad-stark","ok-flaeche","bad-flaeche","warn-flaeche","info-flaeche","stufe-krit","stufe-hoch","stufe-mittel","stufe-niedrig","overlay","schatten","code","code-text","achse","gitter","cat-a","cat-b","cat-c","cat-d","cat-e","cat-f","cat-g","heat-0","heat-1","heat-2","heat-3","heat-4","heat-5","kopf","kopf-flaeche","kopf-text","kopf-linie","rand-aktiv","grund"];
  /* ENDE LISTE */
  var FENSTERROLLEN = ["panel", "panel2", "line", "fg", "fg2", "muted", "hl", "akzent", "warn", "schatten"];
  var PROBE = ["kopf", "flaeche", "feld", "a", "b"];

  var wurzel = document.documentElement;
  var z = { gestartet: false, schluessel: null, standard: null, liste: [], aktuell: null, meldung: null, spiegeln: null, merkt: true };

  function fehler(text) { return new Error("Designkasten: " + text); }
  function pruefe(d, woher) {
    if (!d || typeof d !== "object") throw fehler(woher + ": Eintrag ist kein Objekt");
    if (!/^[a-z][a-z0-9_-]*$/.test(d.id || "")) throw fehler(woher + ": Kennung taugt nicht: " + d.id);
    if (!d.name || !d.text) throw fehler(woher + " " + d.id + ": Name oder Satz fehlt");
    if (d.art !== "Dunkel" && d.art !== "Hell") throw fehler(woher + " " + d.id + ": art muss Dunkel oder Hell sein, ist " + d.art);
    for (var i = 0; i < PROBE.length; i++) if (!d.probe || typeof d.probe[PROBE[i]] !== "string" || !d.probe[PROBE[i]]) throw fehler(woher + " " + d.id + ": Vorschaufarbe fehlt: " + PROBE[i]);
  }
  function kopie(d, herkunft) {
    return { id: d.id, name: d.name, text: d.text, art: d.art, herkunft: herkunft,
      probe: { kopf: d.probe.kopf, flaeche: d.probe.flaeche, feld: d.probe.feld, a: d.probe.a, b: d.probe.b },
      kopfFarbe: d.kopfFarbe || (/^#[0-9a-f]{6}$/i.test(d.probe.kopf) ? d.probe.kopf : null) };
  }
  function finde(id) { for (var i = 0; i < z.liste.length; i++) if (z.liste[i].id === id) return z.liste[i]; return null; }
  function brauchtStart() { if (!z.gestartet) throw fehler("start() wurde noch nicht aufgerufen"); }
  function melde(text) { z.meldung = text; if (global.console && console.warn) console.warn("Designkasten: " + text); }

  function anwende(id) {
    brauchtStart();
    var d = finde(id);
    if (!d) throw fehler("unbekanntes Design: " + id);
    wurzel.setAttribute("data-design", d.id);
    wurzel.setAttribute("data-design-art", d.art === "Hell" ? "hell" : "dunkel");
    wurzel.setAttribute("data-design-herkunft", d.herkunft);
    wurzel.style.colorScheme = d.art === "Hell" ? "light" : "dark";
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta && d.kopfFarbe) meta.setAttribute("content", d.kopfFarbe);
    z.aktuell = d.id;
    if (z.spiegeln) z.spiegeln(d.id, d);
    return d.id;
  }

  function start(o) {
    if (z.gestartet) throw fehler("start() wurde schon aufgerufen");
    o = o || {};
    if (!o.schluessel || typeof o.schluessel !== "string") throw fehler("start(): schluessel fehlt");
    var alle = [], ids = {};
    KASTEN.forEach(function (d) { pruefe(d, "Kasten"); alle.push(kopie(d, "kasten")); ids[d.id] = true; });
    (o.eigene || []).forEach(function (d) {
      pruefe(d, "eigenes Design");
      if (ids[d.id]) throw fehler("eigenes Design " + d.id + " hat dieselbe Kennung wie ein anderes Design");
      alle.push(kopie(d, "eigen")); ids[d.id] = true;
    });
    var reihe = o.auswahl || alle.map(function (d) { return d.id; });
    var gesehen = {};
    z.liste = reihe.map(function (id) {
      if (gesehen[id]) throw fehler("auswahl nennt " + id + " doppelt");
      gesehen[id] = true;
      for (var i = 0; i < alle.length; i++) if (alle[i].id === id) return alle[i];
      throw fehler("auswahl nennt ein Design, das es nicht gibt: " + id);
    });
    (o.eigene || []).forEach(function (d) { if (!gesehen[d.id]) throw fehler("eigenes Design " + d.id + " fehlt in auswahl"); });
    var arten = [];
    z.liste.forEach(function (d) { if (arten[arten.length - 1] !== d.art) arten.push(d.art); });
    if (arten.length > 2) throw fehler("auswahl mischt Dunkel und Hell (" + arten.join(" > ") + "); das Fenster braucht je eine zusammenhaengende Gruppe");
    if (!finde(o.standard)) throw fehler("standard " + o.standard + " steht nicht in auswahl");
    if (o.spiegeln && typeof o.spiegeln !== "function") throw fehler("spiegeln muss eine Funktion sein");
    z.schluessel = o.schluessel; z.standard = o.standard; z.spiegeln = o.spiegeln || null; z.gestartet = true;

    var gemerkt = null;
    try { gemerkt = global.localStorage.getItem(z.schluessel); }
    catch (e) { z.merkt = false; melde("Der Browser sperrt den Speicher - das Design gilt nur bis zum Neuladen (" + e.message + ")."); }
    if (gemerkt && !finde(gemerkt)) melde("Das gemerkte Design „" + gemerkt + "“ gibt es nicht mehr; es gilt " + finde(z.standard).name + ".");
    anwende(gemerkt && finde(gemerkt) ? gemerkt : z.standard);
    if (z.spiegeln && !document.body) document.addEventListener("DOMContentLoaded", function () { z.spiegeln(z.aktuell, finde(z.aktuell)); });
    return z.aktuell;
  }

  function setze(id) {
    anwende(id);
    if (z.merkt) {
      try { global.localStorage.setItem(z.schluessel, z.aktuell); }
      catch (e) { z.merkt = false; melde("Der Browser sperrt den Speicher - das Design gilt nur bis zum Neuladen (" + e.message + ")."); }
    }
    var d = finde(z.aktuell);
    global.dispatchEvent(new CustomEvent("design-gewechselt", { detail: { id: d.id, art: d.art, herkunft: d.herkunft } }));
    return z.aktuell;
  }
  function weiter() { brauchtStart(); var i = z.liste.indexOf(finde(z.aktuell)); return setze(z.liste[(i + 1) % z.liste.length].id); }

  global.Designkasten = {
    kasten: KASTEN.map(function (d) { return kopie(d, "kasten"); }),
    rollen: ROLLEN.slice(),
    fensterrollen: FENSTERROLLEN.slice(),
    start: start, anwende: anwende, setze: setze, weiter: weiter,
    designs: function () { brauchtStart(); return z.liste.map(function (d) { return kopie(d, d.herkunft); }); },
    design: function (id) { brauchtStart(); var d = finde(id === undefined ? z.aktuell : id); return d ? kopie(d, d.herkunft) : null; },
    meldungGelesen: function () { z.meldung = null; },
    get aktuell() { return z.aktuell; },
    get standard() { return z.standard; },
    get meldung() { return z.meldung; },
    get merkt() { return z.merkt; }
  };
})(window);
