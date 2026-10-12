import { useQueryClient } from "@tanstack/react-query";
import { Stack, useRouter } from "expo-router";
import { useState } from "react";
import { Alert, ScrollView, Share, StyleSheet, Switch, Text, View } from "react-native";
import { renameHousehold } from "../../src/api/client";
import { clearPersistedQueryCache } from "../../src/api/queryClient";
import type { HouseholdInvite, HouseholdRole } from "../../src/api/types";
import { useAuth } from "../../src/auth/AuthProvider";
import { InlineQueryState } from "../../src/components/InlineQueryState";
import { OptionList } from "../../src/components/OptionList";
import { PrimaryButton } from "../../src/components/PrimaryButton";
import { ScreenContainer } from "../../src/components/ScreenContainer";
import {
  SegmentedControl,
  type SegmentedControlOption,
} from "../../src/components/SegmentedControl";
import { TextField } from "../../src/components/TextField";
import { Badge, Card, CardLabel, DataRow } from "../../src/components/ui";
import { useServerConfig } from "../../src/config/ServerConfigProvider";
import { useHousehold } from "../../src/household/HouseholdProvider";
import { useAppLock } from "../../src/security/AppLockProvider";
import {
  leading,
  radii,
  space,
  typeScale,
  usePrivacy,
  useTheme,
  useThemeMode,
} from "../../src/theme";
import type { ThemeMode } from "../../src/theme";
import { NotificationsToggleCard } from "../../src/notifications/NotificationsToggleCard";

import {
  useCreateHouseholdInvite,
  useHouseholdInvites,
  useHouseholdMembers,
  useRevokeHouseholdInvite,
} from "../../src/hooks/useHouseholdInvites";
import { useAIHealth } from "../../src/hooks/useAI";
import { useSystemHealth } from "../../src/hooks/useSystemHealth";

// Einstellungen (Auftrag Punkt 4: "bei mehreren [Haushalten]: einfache
// Auswahl, z. B. unter Mehr"): zeigt Profil/Anmeldeinfos und erlaubt bei
// mehreren Haushalten den Wechsel der aktiven Auswahl sowie das Umbenennen
// des aktiven Haushalts (z. B. bei einem Tippfehler beim Anlegen).
export default function EinstellungenScreen() {
  const { userEmail } = useAuth();
  const { me, households, selectedHousehold, selectedHouseholdId, selectHousehold } =
    useHousehold();

  return (
    <>
      <Stack.Screen options={{ title: "Einstellungen" }} />
      <ScreenContainer>
        <ScrollView contentContainerStyle={styles.content}>
          <AppearanceCard />

          <Card>
            <CardLabel>Profil</CardLabel>
            <InfoRow icon="mail-outline" label="E-Mail" value={userEmail ?? "-"} />
            <InfoRow icon="cash-outline" label="Währung" value={me?.currency ?? "-"} />
            <InfoRow
              icon="time-outline"
              label="Zeitzone"
              value={me?.timezone ?? "-"}
              divider={false}
            />
          </Card>

          <ServerCard />

          <Card>
            <CardLabel>Sicherheit</CardLabel>
            <AppLockRow />
          </Card>

          <NotificationsToggleCard />

          <SystemHealthCard />

          {selectedHousehold && selectedHouseholdId ? (
            <Card>
              <CardLabel>Haushalt umbenennen</CardLabel>
              <HouseholdRenameForm
                key={selectedHouseholdId}
                householdId={selectedHouseholdId}
                initialName={selectedHousehold.name}
              />
            </Card>
          ) : null}

          <Card>
            <CardLabel>Haushalt</CardLabel>
            <HouseholdList
              households={households}
              selectedHouseholdId={selectedHouseholdId}
              onSelect={(id) => void selectHousehold(id)}
            />
          </Card>

          {selectedHouseholdId ? (
            <HouseholdMembersCard
              key={selectedHouseholdId}
              isOwner={selectedHousehold?.role === "owner"}
            />
          ) : null}
        </ScrollView>
      </ScreenContainer>
    </>
  );
}

const ROLE_LABELS: Record<string, string> = {
  owner: "Eigentümer",
  editor: "Bearbeiter",
  viewer: "Betrachter",
  child: "Eingeschränkt",
};

const INVITE_ROLE_OPTIONS: { value: HouseholdRole; label: string }[] = [
  { value: "editor", label: "Bearbeiter" },
  { value: "viewer", label: "Betrachter" },
  { value: "child", label: "Eingeschränkt" },
];

const THEME_MODE_OPTIONS: SegmentedControlOption<ThemeMode>[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Hell" },
  { value: "dark", label: "Dunkel" },
];

/**
 * Darstellung: Erscheinungsbild und Privat-Modus.
 *
 * Steht bewusst ganz oben - beides aendert sofort sichtbar die ganze App und
 * ist damit die Einstellung, die man hier am ehesten sucht. Der dunkle Entwurf
 * ist die Gestaltungsabsicht, "System" bleibt trotzdem die Vorgabe.
 */
function AppearanceCard() {
  const theme = useTheme();
  const { mode, setMode } = useThemeMode();
  const privacy = usePrivacy();

  return (
    <Card>
      <CardLabel>Darstellung</CardLabel>

      <View style={styles.field}>
        <Text style={[styles.fieldLabel, { color: theme.textSecondary }]}>Erscheinungsbild</Text>
        <SegmentedControl
          options={THEME_MODE_OPTIONS}
          value={mode}
          onChange={setMode}
          accessibilityLabel="Erscheinungsbild"
        />
      </View>

      <SwitchRow
        title="Privat-Modus"
        hint="Blendet alle Geldbeträge aus, bis du sie wieder einblendest."
        value={privacy.isPrivacyMode}
        onValueChange={privacy.setPrivacyMode}
      />
    </Card>
  );
}

/** Schalterzeile mit Erklaertext - Tippziel 44dp, Zustand auch als Text. */
function SwitchRow({
  title,
  hint,
  value,
  onValueChange,
  disabled = false,
}: {
  title: string;
  hint: string;
  value: boolean;
  onValueChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  const theme = useTheme();

  return (
    <View style={styles.switchRow}>
      <View style={styles.switchText}>
        <Text style={[styles.rowTitle, { color: theme.textPrimary }]}>{title}</Text>
        <Text style={[styles.rowHint, { color: theme.textSecondary }]}>{hint}</Text>
      </View>
      <Switch
        accessibilityRole="switch"
        accessibilityLabel={title}
        accessibilityState={{ checked: value, disabled }}
        value={value}
        onValueChange={onValueChange}
        disabled={disabled}
        trackColor={{ false: theme.surfaceHover, true: theme.positiveStrong }}
        thumbColor={theme.onAccent}
      />
    </View>
  );
}

function InfoRow({
  icon,
  label,
  value,
  divider = true,
}: {
  icon: React.ComponentProps<typeof DataRow>["avatarIcon"];
  label: string;
  value: string;
  divider?: boolean;
}) {
  return <DataRow title={label} subtitle={value} avatarIcon={icon} divider={divider} />;
}

function HouseholdList({
  households,
  selectedHouseholdId,
  onSelect,
}: {
  households: { household_id: string; name: string; role: string }[];
  selectedHouseholdId: string | null;
  onSelect: (householdId: string) => void;
}) {
  const theme = useTheme();
  const router = useRouter();

  return (
    <>
      {households.length === 0 ? (
        <Text style={[styles.rowHint, { color: theme.textSecondary }]}>
          Kein Haushalt vorhanden.
        </Text>
      ) : (
        households.map((household, index) => {
          const selected = household.household_id === selectedHouseholdId;
          return (
            <DataRow
              key={household.household_id}
              title={household.name}
              subtitle={ROLE_LABELS[household.role] ?? household.role}
              avatarLabel={household.name}
              // "Aktiv" steht als Text neben dem Namen: eine rein farbige
              // Markierung waere fuer farbfehlsichtige Nutzer nicht lesbar.
              titleBadge={selected ? <Badge tone="positive" label="Aktiv" size="small" /> : null}
              accessibilityLabel={
                selected ? `${household.name}, aktiver Haushalt` : `${household.name} auswählen`
              }
              onPress={() => onSelect(household.household_id)}
              divider={index < households.length - 1}
            />
          );
        })
      )}

      <PrimaryButton
        label="Weiteren Haushalt erstellen"
        variant="secondary"
        onPress={() => router.push("/onboarding")}
      />
    </>
  );
}

function SystemHealthCard() {
  const theme = useTheme();
  const { data: systemHealth, isLoading: isSystemLoading } = useSystemHealth();
  const { data: aiHealth, isLoading: isAILoading } = useAIHealth();

  const systemOk = systemHealth?.status === "ok";
  const aiOk = !!aiHealth?.is_connected;

  return (
    <Card>
      <CardLabel>Systemgesundheit</CardLabel>

      <DataRow
        title="System-API"
        avatarIcon="server-outline"
        trailing={
          isSystemLoading ? (
            <Badge tone="neutral" label="Lädt…" />
          ) : (
            <Badge
              tone={systemOk ? "positive" : "warning"}
              label={systemOk ? "Erreichbar" : "Nicht erreichbar"}
            />
          )
        }
      />

      <DataRow
        title="LM Studio KI"
        subtitle={aiOk && aiHealth?.active_model ? `Modell: ${aiHealth.active_model}` : undefined}
        avatarIcon="sparkles-outline"
        divider={false}
        trailing={
          isAILoading ? (
            <Badge tone="neutral" label="Lädt…" />
          ) : (
            <Badge
              tone={aiOk ? "positive" : "warning"}
              label={aiOk ? "Verbunden" : "Nicht verbunden"}
            />
          )
        }
      />

      <Text style={[styles.rowHint, { color: theme.textMuted }]}>
        Ohne KI-Verbindung bleiben Kategorievorschläge aus; alles andere funktioniert weiter.
      </Text>
    </Card>
  );
}

function InviteRow({ invite, onRevoke }: { invite: HouseholdInvite; onRevoke: () => void }) {
  const theme = useTheme();

  async function handleShare() {
    await Share.share({
      message:
        `Einladung zum Finanzplaner-Haushalt: Code ${invite.code} ` +
        `(als ${ROLE_LABELS[invite.role] ?? invite.role}, gültig bis ` +
        `${new Date(invite.expires_at).toLocaleDateString("de-DE")})`,
    });
  }

  return (
    <View style={[styles.inviteTile, { backgroundColor: theme.surfaceRaised }]}>
      <Text style={[styles.inviteCode, { color: theme.textPrimary }]}>{invite.code}</Text>
      <Text style={[styles.rowHint, { color: theme.textSecondary }]}>
        {ROLE_LABELS[invite.role] ?? invite.role} · gültig bis{" "}
        {new Date(invite.expires_at).toLocaleDateString("de-DE")}
      </Text>
      <View style={styles.inviteActions}>
        <View style={styles.inviteAction}>
          <PrimaryButton
            label="Teilen"
            variant="secondary"
            size="compact"
            onPress={() => void handleShare()}
          />
        </View>
        <View style={styles.inviteAction}>
          <PrimaryButton label="Widerrufen" variant="danger" size="compact" onPress={onRevoke} />
        </View>
      </View>
    </View>
  );
}

function HouseholdMembersCard({ isOwner }: { isOwner: boolean }) {
  const router = useRouter();
  const [inviteRole, setInviteRole] = useState<HouseholdRole>("editor");
  const { data: members, isLoading: membersLoading, isError: membersError } = useHouseholdMembers();
  const { data: invites } = useHouseholdInvites(isOwner);
  const createInviteMutation = useCreateHouseholdInvite();
  const revokeInviteMutation = useRevokeHouseholdInvite();

  const openInvites = (invites ?? []).filter((i) => !i.used_at && !i.revoked_at);
  const memberList = members ?? [];

  return (
    <Card>
      <CardLabel>Mitglieder & Einladen</CardLabel>

      {membersLoading ? (
        <InlineQueryState status="loading" skeletonRows={2} skeletonRowHeight={20} />
      ) : membersError ? (
        <InlineQueryState
          status="error"
          title="Mitglieder konnten nicht geladen werden"
          message="Bitte prüfe deine Verbindung und öffne die Einstellungen erneut."
        />
      ) : memberList.length === 0 ? (
        <InlineQueryState status="empty" title="Noch keine weiteren Mitglieder" />
      ) : (
        memberList.map((member, index) => (
          <DataRow
            key={member.user_id}
            title={member.display_name || member.user_id}
            avatarLabel={member.display_name || member.user_id}
            trailing={<Badge tone="neutral" label={ROLE_LABELS[member.role] ?? member.role} />}
            divider={index < memberList.length - 1}
          />
        ))
      )}

      <PrimaryButton
        label="Einladungscode einlösen"
        variant="secondary"
        onPress={() => router.push("/beitreten")}
      />

      {isOwner ? (
        <View style={styles.inviteBlock}>
          <CardLabel>Rolle für neue Einladung</CardLabel>
          <OptionList
            options={INVITE_ROLE_OPTIONS}
            value={inviteRole}
            onChange={setInviteRole}
            accessibilityLabel="Rolle für neue Einladung"
          />
          <PrimaryButton
            label="Code erstellen"
            onPress={() => createInviteMutation.mutate(inviteRole)}
            loading={createInviteMutation.isPending}
          />

          {openInvites.map((invite) => (
            <InviteRow
              key={invite.id}
              invite={invite}
              onRevoke={() => revokeInviteMutation.mutate(invite.id)}
            />
          ))}
        </View>
      ) : null}
    </Card>
  );
}

// Eigene, per householdId geschluesselte Komponente: beim Haushaltswechsel
// remounted React die Komponente und der lokale State startet frisch mit
// dem neuen Namen - ohne useEffect+setState-Kaskade (gleiches Muster wie
// die "effectiveHouseholdId"-Ableitung in HouseholdProvider.tsx).
function HouseholdRenameForm({
  householdId,
  initialName,
}: {
  householdId: string;
  initialName: string;
}) {
  const { accessToken } = useAuth();
  const { refresh } = useHousehold();
  const [name, setName] = useState(initialName);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function handleSave() {
    const trimmed = name.trim();
    if (trimmed.length === 0) {
      setError("Bitte einen Namen angeben.");
      return;
    }
    if (!accessToken) return;

    setSubmitting(true);
    setError(null);
    setSaved(false);
    try {
      await renameHousehold(accessToken, householdId, trimmed);
      await refresh();
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Haushalt konnte nicht umbenannt werden.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View style={styles.field}>
      <TextField
        label="Name des Haushalts"
        value={name}
        onChangeText={(value) => {
          setName(value);
          setSaved(false);
        }}
      />
      {error ? <ErrorText message={error} /> : null}
      {saved && !error ? <Badge tone="positive" label="Gespeichert" /> : null}
      <PrimaryButton label="Speichern" onPress={() => void handleSave()} loading={submitting} />
    </View>
  );
}

function ErrorText({ message }: { message: string }) {
  const theme = useTheme();
  return (
    <Text accessibilityRole="alert" style={[styles.rowHint, { color: theme.warning }]}>
      {message}
    </Text>
  );
}

function AppLockRow() {
  const appLock = useAppLock();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleChange() {
    if (pending) return;
    setError(null);
    setPending(true);
    try {
      if (appLock.isEnabled) {
        await appLock.disable();
      } else {
        const result = await appLock.enable();
        if (!result.success) {
          setError(
            result.reason === "not_supported"
              ? "Keine Biometrie oder Geräte-PIN eingerichtet."
              : "Bestätigung fehlgeschlagen oder abgebrochen.",
          );
        }
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <SwitchRow
        title="App-Sperre (Biometrie/PIN)"
        hint="Fragt beim Öffnen der App nach deinem Fingerabdruck, Gesicht oder der Geräte-PIN."
        value={appLock.isEnabled}
        onValueChange={() => void handleChange()}
        disabled={pending}
      />
      {error ? <ErrorText message={error} /> : null}
    </>
  );
}

// Zeigt, mit welchem Server die App gerade spricht (Cloud oder Eigener
// Server, siehe src/config/ServerConfigProvider.tsx) und erlaubt den
// Wechsel. "Server wechseln" meldet zuerst ab und leert den Lesecache
// (gleiches Muster wie beim regulaeren Abmelden in app/_layout.tsx) -
// sonst koennten Fachdaten des alten Servers kurzzeitig unter dem neuen
// auftauchen.
function ServerCard() {
  const serverConfig = useServerConfig();
  const { signOut } = useAuth();
  const queryClient = useQueryClient();
  const [switching, setSwitching] = useState(false);

  const config = serverConfig.config;
  const label =
    config?.mode === "cloud"
      ? "Finanzplaner Cloud"
      : (config?.instanceDisplayName ?? config?.apiBaseUrl ?? "-");

  function handleSwitchServer() {
    Alert.alert(
      "Server wechseln?",
      "Du wirst abgemeldet und musst dich am neuen Server neu anmelden.",
      [
        { text: "Abbrechen", style: "cancel" },
        {
          text: "Wechseln",
          style: "destructive",
          onPress: () => {
            setSwitching(true);
            (async () => {
              try {
                await signOut();
              } catch {
                // Best effort: auch bei einer bereits ungueltigen Session soll
                // der Wechsel weitergehen statt haengen zu bleiben.
              }
              queryClient.clear();
              await clearPersistedQueryCache();
              await serverConfig.reset();
            })().finally(() => setSwitching(false));
          },
        },
      ],
    );
  }

  return (
    <Card>
      <CardLabel>Server</CardLabel>
      <InfoRow icon="cloud-outline" label="Verbunden mit" value={label} divider={false} />
      <PrimaryButton
        label="Server wechseln"
        onPress={handleSwitchServer}
        loading={switching}
        variant="secondary"
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: space.lg,
    paddingBottom: space.xxxl,
    gap: space.lg,
  },
  field: {
    gap: space.sm,
  },
  fieldLabel: {
    fontSize: typeScale.caption,
    fontWeight: "600",
  },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: space.md,
    minHeight: 44,
  },
  switchText: {
    flex: 1,
    gap: space.hair,
  },
  rowTitle: {
    fontSize: typeScale.base,
    fontWeight: "700",
  },
  rowHint: {
    fontSize: typeScale.caption,
    lineHeight: leading(typeScale.caption),
  },
  inviteBlock: {
    gap: space.md,
  },
  inviteTile: {
    borderRadius: radii.md,
    padding: space.md,
    gap: space.xs,
  },
  inviteCode: {
    fontSize: typeScale.medium,
    fontWeight: "700",
    letterSpacing: 1,
  },
  inviteActions: {
    flexDirection: "row",
    gap: space.sm,
    marginTop: space.xs,
  },
  inviteAction: {
    flex: 1,
  },
});
