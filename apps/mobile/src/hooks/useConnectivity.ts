import NetInfo from "@react-native-community/netinfo";
import { onlineManager } from "@tanstack/react-query";
import { useEffect, useState } from "react";

// Phase 9 (Offline-Kern): koppelt den onlineManager von React Query an
// NetInfo. Dadurch pausieren alle bestehenden Queries im Flugmodus
// automatisch (networkMode "online" ist der v5-Default) und laufen bei
// Reconnect von selbst wieder an - inklusive des 30s-Polls in
// useSystemHealth.ts, der sonst im Offline-Betrieb im Leerlauf Fehler
// produzieren wuerde.
//
// Der Queue-Flush haengt bewusst NICHT allein an diesem Signal: "das
// Betriebssystem meldet eine Verbindung" ist kein Beweis, dass die eigene API
// erreichbar ist (Captive Portal, VPN, Server unten). Deshalb entscheidet
// ueber Wiederholungen weiterhin der tatsaechliche Request-Ausgang samt
// Backoff (siehe ../offline/queueProcessor.ts).

/**
 * Nur einmal pro App-Laufzeit verkabeln. onlineManager.setEventListener()
 * ersetzt einen vorher gesetzten Listener - ein zweiter Aufruf wuerde den
 * ersten stillschweigend abmelden.
 */
let wired = false;

function wireOnlineManagerOnce(): void {
  if (wired) return;
  wired = true;
  onlineManager.setEventListener((setOnline) =>
    NetInfo.addEventListener((state) => {
      setOnline(state.isConnected ?? false);
    }),
  );
}

/**
 * true, sobald das Betriebssystem eine Netzwerkverbindung meldet. Kein Beweis,
 * dass die API erreichbar ist - fuer reine Anzeigezwecke ("offline"-Hinweis)
 * und als Ausloeser fuer einen Flush-Versuch gedacht.
 */
export function useConnectivity(): boolean {
  const [isConnected, setIsConnected] = useState(true);

  useEffect(() => {
    wireOnlineManagerOnce();
    // Der Listener feuert unmittelbar mit dem aktuellen Zustand, ein
    // separates fetch() ist deshalb nicht noetig.
    return NetInfo.addEventListener((state) => {
      setIsConnected(state.isConnected ?? false);
    });
  }, []);

  return isConnected;
}
