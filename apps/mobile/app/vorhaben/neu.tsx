import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import type { MajorProjectCreateInput, ProjectType } from "../../src/api/types";
import { OptionList } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import { TextField } from "../../src/components/TextField";
import { Card } from "../../src/components/ui";
import { useCreateProject } from "../../src/hooks/useProjects";
import { leading, space, typeScale, useTheme } from "../../src/theme";

/**
 * Neues, einfaches Vorhaben anlegen
 * (docs/VORHABEN-GROSSINVESTITIONEN-PLAN.md, Abschnitt 9, P5).
 *
 * Bewusst minimal: nur Name und Art, Waehrung/Land bleiben Serverdefaults
 * (EUR/DE, siehe MajorProjectCreate). Kostenplan, Finanzierung und Foerderung
 * werden anschliessend im Vorhaben-Detail bzw. im Web-Kostenplan ausgebaut -
 * kein Vorlagenpicker und kein Masseneditor auf Mobile.
 */

const TYPE_OPTIONS: { value: ProjectType; label: string }[] = [
  { value: "home_build", label: "Hausbau" },
  { value: "property_purchase", label: "Immobilienkauf" },
  { value: "renovation", label: "Sanierung" },
  { value: "vehicle", label: "Fahrzeug" },
  { value: "solar", label: "Photovoltaik" },
  { value: "education", label: "Ausbildung" },
  { value: "business", label: "Gründung" },
  { value: "other", label: "Sonstiges" },
];

export default function VorhabenNeuScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [name, setName] = useState("");
  const [projectType, setProjectType] = useState<ProjectType>("home_build");
  const [error, setError] = useState<string | null>(null);
  const createProject = useCreateProject();

  const handleSave = () => {
    if (!name.trim()) {
      setError("Bitte einen Namen eintragen.");
      return;
    }
    setError(null);

    const input: MajorProjectCreateInput = {
      name: name.trim(),
      project_type: projectType,
    };

    createProject.mutate(input, {
      onSuccess: (project) => router.replace(`/vorhaben/${project.id}`),
      onError: (mutationError) => setError(mutationError.message),
    });
  };

  return (
    <>
      <Stack.Screen options={{ title: "Neues Vorhaben" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <TextField
            label="Name"
            value={name}
            onChangeText={setName}
            placeholder="z. B. Hausbau Musterweg"
          />

          <View style={styles.field}>
            <Text style={[styles.label, { color: theme.textSecondary }]}>Art</Text>
            <OptionList options={TYPE_OPTIONS} value={projectType} onChange={setProjectType} />
          </View>

          <Text style={[styles.hint, { color: theme.textSecondary }]}>
            Kostenpositionen, Finanzierung und Foerderung lassen sich danach im Vorhaben-Detail
            ansehen und im Web-Kostenplan weiter ausbauen.
          </Text>

          {error ? (
            <Card tone="warning" density="compact">
              <Text style={[styles.hint, { color: theme.warning }]} accessibilityRole="alert">
                {error}
              </Text>
            </Card>
          ) : null}

          <PrimaryButton
            label="Vorhaben anlegen"
            onPress={handleSave}
            loading={createProject.isPending}
          />
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    gap: space.lg,
  },
  field: {
    gap: space.sm,
  },
  label: {
    fontSize: typeScale.body,
    fontWeight: "600",
  },
  hint: {
    fontSize: typeScale.body,
    lineHeight: leading(typeScale.body),
  },
});
