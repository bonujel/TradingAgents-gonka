/**
 * Global route guard.
 *
 * - Unauthenticated users are bounced to /login (except when already there).
 * - Authenticated users hitting /login are sent to the dashboard.
 * - /account is admin-only; /profile is normal-user-only (the super admin's
 *   password rotates on restart, so it has nothing to reset).
 */
export default defineNuxtRouteMiddleware((to) => {
  // The session token lives in localStorage, which only exists in the
  // browser. Skip on the server — the client guard re-runs on hydration
  // and performs the real redirect. Protected pages fetch their data in
  // onMounted (client-only), so nothing sensitive loads server-side.
  if (import.meta.server) return;

  const auth = useAuthStore();
  auth.hydrate();

  if (to.path === "/login") {
    if (auth.isAuthenticated) return navigateTo("/");
    return;
  }

  if (!auth.isAuthenticated) {
    return navigateTo("/login");
  }

  if (to.path.startsWith("/account") && !auth.isAdmin) {
    return navigateTo("/");
  }
  if (to.path.startsWith("/profile") && auth.isAdmin) {
    return navigateTo("/");
  }
});
