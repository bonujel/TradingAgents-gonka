/**
 * Global route guard.
 *
 * - /dashboard is public (no token needed)
 * - / for anonymous users → /dashboard (so the bare domain works for guests)
 * - any private path for anonymous users → /dashboard?login=1&return_to=...
 *   (dashboard page reads ?login=1 and opens the in-place login modal,
 *   then on success navigates to return_to)
 * - /login behaves as before (authenticated users bounced to /)
 * - admin/profile gating preserved for logged-in users
 */

const PUBLIC_PATHS = ["/dashboard"];
const REDIRECT_ROOT = ["/"];
const ADMIN_ONLY_PATHS = ["/tasks", "/schedule", "/settings", "/account"];

export default defineNuxtRouteMiddleware((to) => {
  if (import.meta.server) return;

  const auth = useAuthStore();
  auth.hydrate();

  if (to.path === "/login") {
    if (auth.isAuthenticated) return navigateTo("/");
    return;
  }

  if (PUBLIC_PATHS.includes(to.path)) return;

  if (!auth.isAuthenticated) {
    if (REDIRECT_ROOT.includes(to.path)) return navigateTo("/dashboard");
    return navigateTo({
      path: "/dashboard",
      query: { login: "1", return_to: to.fullPath },
    });
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
