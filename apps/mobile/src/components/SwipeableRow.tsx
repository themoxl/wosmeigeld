import { type PropsWithChildren, useRef, useState } from "react";
import { Animated, PanResponder, Pressable, StyleSheet, Text, View } from "react-native";
import { space, typeScale } from "../theme";

export type SwipeAction = {
  key: string;
  label: string;
  backgroundColor: string;
  textColor: string;
  onPress: () => void;
};

type Props = PropsWithChildren<{
  actions: SwipeAction[];
  // Undurchsichtige Hintergrundfarbe der Vordergrund-Zeile (z. B.
  // theme.background), noetig damit die dahinterliegenden Aktionsflaechen im
  // geschlossenen Zustand (translateX === 0) nicht durchscheinen - React
  // Native ueberlagert spaeter deklarierte Geschwisterelemente zwar im
  // Zeichenverlauf, das ersetzt aber keine eigene Deckfarbe.
  backgroundColor: string;
}>;

const ACTION_WIDTH = 88;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

/**
 * Wischaktionen fuer Listenzeilen (Auftrag Punkt 1: Kategorie aendern,
 * Loeschen). Statt einer neuen Abhaengigkeit (z. B.
 * react-native-gesture-handler's Swipeable) nutzt diese Komponente
 * ausschliesslich React Natives eingebaute Animated-/PanResponder-APIs -
 * beides ist Teil des react-native-Kernpakets und in diesem Projekt bereits
 * verfuegbar, sodass keine zusaetzliche Bibliothek noetig ist. Ein Wisch nach
 * links (dx < 0) legt bis zu N feste Aktionsflaechen rechts frei; ueber die
 * Halbwegs-Schwelle hin- oder herschnappen (Animated.spring), analog zum
 * iOS-/Android-Standardverhalten fuer Listenwischaktionen.
 */
export function SwipeableRow({ actions, backgroundColor, children }: Props) {
  // Animated.Value muss ueber die Lebensdauer der Komponente stabil
  // (einmalig erzeugt) bleiben, aber nicht ueber ref.current waehrend des
  // Renderns gelesen werden. useState(() => ...) mit Lazy-Initializer
  // erzeugt denselben stabilen Einzelwert ueber Renders hinweg, ist aber ein
  // ganz normaler (waehrend des Renderns lesbarer) State-Wert.
  const [translateX] = useState(() => new Animated.Value(0));
  // openOffset muss ueber die Lebensdauer der Komponente persistieren
  // (ueberlebt Re-Renders waehrend eines laufenden Wischgestus), wird aber
  // ausschliesslich innerhalb von PanResponder-Event-Handlern gelesen/
  // geschrieben, nie waehrend des Renderns selbst - der Lehrbuchfall fuer
  // useRef.
  const openOffset = useRef(0);
  const maxSwipe = -(ACTION_WIDTH * actions.length);

  // PanResponder.create(...) wird bewusst bei jedem Render neu aufgerufen
  // statt in useRef/useState memoisiert: PanResponder ist eine reine
  // Factory, die nur eine Handler-Props-Struktur zusammenbaut (React Native
  // liest panHandlers bei jedem Touch-Event frisch von den aktuellen
  // View-Props), Neuaufbau bei jedem Render ist dafuer unproblematisch. Die
  // Handler-Funktionen unten (onPanResponderMove/-Release/-Terminate), die
  // openOffset.current lesen/schreiben, werden NICHT synchron durch
  // PanResponder.create() selbst aufgerufen, sondern erst spaeter durch das
  // native Touch-/Responder-System bei tatsaechlichen Gesten - also
  // klassische Event-Handler, keine Ref-Lesung waehrend des Renderns.
  const panResponder = PanResponder.create({
    onMoveShouldSetPanResponderCapture: (_, gesture) =>
      Math.abs(gesture.dx) > 8 && Math.abs(gesture.dx) > Math.abs(gesture.dy) * 1.5,
    onPanResponderMove: (_, gesture) => {
      const next = clamp(openOffset.current + gesture.dx, maxSwipe, 0);
      translateX.setValue(next);
    },
    onPanResponderRelease: (_, gesture) => {
      const next = clamp(openOffset.current + gesture.dx, maxSwipe, 0);
      const shouldOpen = next <= maxSwipe / 2;
      const target = shouldOpen ? maxSwipe : 0;
      openOffset.current = target;
      Animated.spring(translateX, {
        toValue: target,
        useNativeDriver: true,
        bounciness: 0,
      }).start();
    },
    onPanResponderTerminate: () => {
      openOffset.current = 0;
      Animated.spring(translateX, { toValue: 0, useNativeDriver: true, bounciness: 0 }).start();
    },
  });

  function close() {
    openOffset.current = 0;
    Animated.spring(translateX, { toValue: 0, useNativeDriver: true, bounciness: 0 }).start();
  }

  if (actions.length === 0) {
    return <>{children}</>;
  }

  return (
    <View style={styles.container}>
      <View style={[styles.actionsRow, { width: ACTION_WIDTH * actions.length }]}>
        {actions.map((action) => (
          <Pressable
            key={action.key}
            accessibilityRole="button"
            accessibilityLabel={action.label}
            onPress={() => {
              close();
              action.onPress();
            }}
            style={({ pressed }) => [
              styles.action,
              {
                width: ACTION_WIDTH,
                backgroundColor: action.backgroundColor,
                opacity: pressed ? 0.8 : 1,
              },
            ]}
          >
            <Text style={[styles.actionLabel, { color: action.textColor }]} numberOfLines={2}>
              {action.label}
            </Text>
          </Pressable>
        ))}
      </View>

      <Animated.View
        style={[styles.foreground, { backgroundColor, transform: [{ translateX }] }]}
        {...panResponder.panHandlers}
      >
        {children}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    // Bewusst kein "overflow: hidden" hier - würde auf manchen RN-Versionen
    // den absolut positionierten actionsRow beschneiden, bevor die
    // Foreground-View ihn per translateX freigibt. Da actionsRow rechts
    // buendig unter der Foreground-View liegt und dieselbe Breite wie die
    // sichtbare Liste hat, ist zusaetzliches Clipping nicht noetig.
    justifyContent: "center",
  },
  actionsRow: {
    position: "absolute",
    right: 0,
    top: 0,
    bottom: 0,
    flexDirection: "row",
  },
  action: {
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.sm,
  },
  actionLabel: {
    fontSize: typeScale.body,
    fontWeight: "600",
    textAlign: "center",
  },
  foreground: {},
});
