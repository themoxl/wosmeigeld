/* Designkasten-Stand 1a08d42ce51c · Quelle: sia_public/designkasten · hier nicht aendern, sondern dort und dann node designkasten/verteilen.js */
/* Designkasten · designwahl.js — das Fenster am ◐: alle Designs zur Wahl statt Durchklicken.

   Nachbau von SIAs src/core/designwahl.js, ohne JET-Namensraum. Die Liste kommt aus
   Designkasten.designs() (designs.js, vorher gestartet); gewaehlt wird ueber Designkasten.setze().
   Das Fenster bleibt nach der Wahl offen, damit man vergleichen kann.

     Designwahl.oeffne(anker)      Fenster unter dem Anker (rechtsbuendig), Fokus auf das aktive Design
     Designwahl.umschalten(anker)  oeffnen oder schliessen
     Designwahl.schliesse()        Esc und Klick daneben schliessen auch
     Designwahl.einbetten(ziel)    dieselbe Liste in ein vorhandenes Element (z. B. ein Ansicht-Fenster);
                                   gibt { aktualisiere, entferne } zurueck

   Der Anker bekommt aria-expanded; aria-haspopup="menu" setzt das Programm im HTML. */
(function (global) {
  "use strict";
  var pop = null, anker = null, eingebettet = [];

  function kasten() {
    var k = global.Designkasten;
    if (!k || typeof k.designs !== "function") throw new Error("Designwahl: designs.js fehlt oder ist nicht geladen.");
    return k;
  }
  function teil(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function zeile(d, standard) {
    var b = teil("button", "dk-item"); b.type = "button"; b.dataset.design = d.id; b.setAttribute("role", "menuitemradio");
    var probe = teil("span", "dk-probe"); probe.setAttribute("aria-hidden", "true"); probe.style.background = d.probe.flaeche;
    [["dk-p-kopf", d.probe.kopf], ["dk-p-feld", d.probe.feld], ["dk-p-a", d.probe.a], ["dk-p-b", d.probe.b]].forEach(function (p) {
      var i = teil("i", p[0]); i.style.background = p[1]; probe.appendChild(i);
    });
    var text = teil("span", "dk-text"), name = teil("b", "", d.name);
    if (d.id === standard) name.appendChild(teil("em", "", "Standard"));
    text.appendChild(name); text.appendChild(teil("small", "", d.text));
    var haken = teil("span", "dk-haken", "✓"); haken.setAttribute("aria-hidden", "true");
    b.appendChild(probe); b.appendChild(text); b.appendChild(haken);
    b.addEventListener("click", function () { kasten().setze(d.id); });
    return b;
  }
  function fuelle(ziel) {
    var k = kasten(), art = "";
    if (k.meldung) ziel.appendChild(teil("div", "dk-meldung", k.meldung));
    k.designs().forEach(function (d) {
      if (d.art !== art) { art = d.art; ziel.appendChild(teil("div", "dk-gruppe", art)); }
      ziel.appendChild(zeile(d, k.standard));
    });
  }
  function kopf() {
    var k = teil("div", "dk-kopf");
    k.appendChild(teil("span", "", "Design"));
    k.appendChild(teil("small", "", kasten().merkt ? "wird gemerkt" : "wird nicht gemerkt"));
    return k;
  }
  function markiere() {
    var aktiv = kasten().aktuell;
    document.querySelectorAll(".dk-item").forEach(function (b) { b.setAttribute("aria-checked", String(b.dataset.design === aktiv)); });
  }
  function tasten(behaelter, beiEsc) {
    behaelter.addEventListener("keydown", function (e) {
      var items = Array.prototype.slice.call(behaelter.querySelectorAll(".dk-item")), i = items.indexOf(document.activeElement);
      if (!items.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus(); }
      else if (e.key === "Home" || e.key === "End") { e.preventDefault(); items[e.key === "Home" ? 0 : items.length - 1].focus(); }
      else if (e.key === "Escape" && beiEsc) { e.preventDefault(); beiEsc(); }
    });
  }

  function baue() {
    pop = teil("div", "dk-wahl"); pop.id = "dkDesignwahl"; pop.hidden = true;
    pop.setAttribute("role", "menu"); pop.setAttribute("aria-label", "Design wählen");
    tasten(pop, function () { var zurueck = anker; schliesse(); if (zurueck) zurueck.focus(); });
    pop.addEventListener("focusout", function (e) { if (e.relatedTarget && !pop.contains(e.relatedTarget) && e.relatedTarget !== anker) schliesse(); });
    document.body.appendChild(pop);
  }
  function neuFuellen() { pop.textContent = ""; pop.appendChild(kopf()); fuelle(pop); }
  // Unter dem Anker, rechtsbuendig; passt es unten nicht hin, darueber.
  function lage() {
    var r = anker.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
    var left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8);
    var top = r.bottom + 6; if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    pop.style.left = Math.round(left) + "px"; pop.style.top = Math.round(top) + "px";
  }
  function oeffne(el) {
    if (!el) throw new Error("Designwahl.oeffne: Anker fehlt.");
    if (anker && anker !== el) anker.setAttribute("aria-expanded", "false");
    anker = el;
    if (!pop) baue();
    neuFuellen();
    pop.hidden = false; lage(); markiere();
    anker.setAttribute("aria-expanded", "true");
    (pop.querySelector('.dk-item[aria-checked="true"]') || pop.querySelector(".dk-item")).focus();
  }
  function schliesse() {
    if (pop) pop.hidden = true;
    if (anker) anker.setAttribute("aria-expanded", "false");
    anker = null;
  }
  function offen() { return !!(pop && !pop.hidden); }
  function umschalten(el) { if (offen() && anker === el) schliesse(); else oeffne(el); }

  function einbetten(ziel) {
    if (!ziel) throw new Error("Designwahl.einbetten: Ziel fehlt.");
    var box = teil("div", "dk-wahl dk-eingebettet"); box.setAttribute("role", "menu"); box.setAttribute("aria-label", "Design wählen");
    tasten(box, null);
    function aktualisiere() { box.textContent = ""; fuelle(box); markiere(); }
    ziel.appendChild(box);   // erst einhaengen: markiere() sucht im Dokument
    aktualisiere();
    var eintrag = { box: box, aktualisiere: aktualisiere };
    eingebettet.push(eintrag);
    return { aktualisiere: aktualisiere, entferne: function () { box.remove(); eingebettet = eingebettet.filter(function (x) { return x !== eintrag; }); } };
  }

  document.addEventListener("pointerdown", function (e) { if (offen() && !pop.contains(e.target) && !(anker && anker.contains(e.target))) schliesse(); });
  global.addEventListener("resize", function () { if (offen()) lage(); });
  global.addEventListener("design-gewechselt", markiere);
  global.Designwahl = { oeffne: oeffne, schliesse: schliesse, umschalten: umschalten, offen: offen, einbetten: einbetten };
})(window);
