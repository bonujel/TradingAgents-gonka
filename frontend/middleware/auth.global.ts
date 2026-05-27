/**
 * Global route guard.
 *
 * - /dashboard is public (no token needed)
 * - /decisions/{ticker}/{trade_date} detail pages are public too — the
 *   dashboard's ticker cards deep-link straight into them
 * - / for anonymous users → /dashboard (so the bare domain works for guests)
 * - any other private path for anonymous users → /dashboard?login=1&return_to=...
 *   (dashboard page reads ?login=1 and opens the in-place login modal,
 *   then on success navigates to return_to)
 * - /login behaves as before (authenticated users bounced to /)
 * - admin/profile gating preserved for logged-in users
 */

const PUBLIC_PATHS = ["/dashboard"];
const REDIRECT_ROOT = ["/"];
const ADMIN_ONLY_PATHS = ["/tasks", "/schedule", "/settings", "/account"];

function isPublicPath(path: string): boolean {
  if (PUBLIC_PATHS.includes(path)) return true;
  // /decisions/{ticker}/{trade_date} detail pages are public so the
  // dashboard ticker cards deep-link straight in. Anything under
  // /decisions/X/Y... counts; the list root /decisions doesn't exist.
  if (path.startsWith("/decisions/")) return true;
  return false;
}

export default defineNuxtRouteMiddleware((to) => {
  if (import.meta.server) return;

  const auth = useAuthStore();
  auth.hydrate();

  if (to.path === "/login") {
    if (auth.isAuthenticated) return navigateTo("/");
    return;
  }

  if (isPublicPath(to.path)) return;

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
