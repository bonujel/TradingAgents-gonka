/**
 * Global route guard.
 *
 * - Unauthenticated users are bounced to /login (except when already there).
 * - Authenticated users hitting /login are sent to the dashboard.
 * - Tasks / Schedule / Settings / Account Management are super-admin
 *   only — normal users can only browse Decisions and tweak their own
 *   Personal Settings (password). Backend enforces the same fence via
 *   ``Depends(auth.require_admin)`` so this guard isn't the security
 *   boundary, just the UX one.
 * - /profile is normal-user-only (the super admin's password rotates on
 *   restart, so it has nothing to reset).
 */

// Paths that require the admin role. ``startsWith`` matches the page itself
// plus any nested route (e.g. /tasks/foo) so we don't have to enumerate.
const ADMIN_ONLY_PATHS = ["/tasks", "/schedule", "/settings", "/account"];

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

  if (
    !auth.isAdmin &&
    ADMIN_ONLY_PATHS.some((p) => to.path === p || to.path.startsWith(`${p}/`))
  ) {
    return navigateTo("/");
  }
  if (to.path.startsWith("/profile") && auth.isAdmin) {
    return navigateTo("/");
  }
});
