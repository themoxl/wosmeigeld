import * as Notifications from "expo-notifications";

// Modulebene-Seiteneffekt beim ersten Import, gleiches Idiom wie der
// AppState-Listener in ../auth/supabase.ts. Legt fest, wie eine
// Benachrichtigung angezeigt wird, waehrend die App im Vordergrund ist.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: true,
  }),
});
