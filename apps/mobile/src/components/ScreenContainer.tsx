import type { PropsWithChildren } from "react";
import { StyleSheet } from "react-native";
import { SafeAreaView, type Edge } from "react-native-safe-area-context";
import { useTheme } from "../theme";

type Props = PropsWithChildren<{
  /**
   * Vorgabe ist NUR links/rechts: die allermeisten Bildschirme liegen in
   * einem Stack mit nativer Kopfzeile, die den oberen Rand bereits
   * freihaelt - ein zweites Mal freihalten ergaebe einen leeren Streifen.
   *
   * Die fuenf Tab-Bildschirme haben seit dem Redesign keine native Kopfzeile
   * mehr (siehe app/(tabs)/_layout.tsx) und geben deshalb ausdruecklich
   * `edges={SCREEN_EDGES_WITH_TOP}` an.
   */
  edges?: readonly Edge[];
}>;

const DEFAULT_EDGES: readonly Edge[] = ["left", "right"];

/** Fuer Bildschirme ohne native Kopfzeile (Tab-Wurzeln). */
export const SCREEN_EDGES_WITH_TOP: readonly Edge[] = ["top", "left", "right"];

/** Themed Grundcontainer fuer Screens: respektiert Safe-Area und Hell-/Dunkelmodus. */
export function ScreenContainer({ children, edges = DEFAULT_EDGES }: Props) {
  const theme = useTheme();

  return (
    <SafeAreaView style={[styles.container, { backgroundColor: theme.background }]} edges={edges}>
      {children}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
});
