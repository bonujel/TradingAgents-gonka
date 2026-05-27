<template>
  <div class="dashboard-bg pointer-events-none fixed inset-0 -z-10" aria-hidden="true">
    <!-- Radial dark gradient base -->
    <div class="absolute inset-0 dashboard-bg-radial" />
    <!-- Hex grid pattern overlay -->
    <div class="absolute inset-0 dashboard-bg-hex" />
    <!-- Drifting particles -->
    <div
      v-for="(p, i) in particles"
      :key="i"
      class="dashboard-particle"
      :style="{
        left: p.left + '%',
        top: p.top + '%',
        animationDelay: p.delay + 's',
        animationDuration: p.duration + 's',
      }"
    />
    <!-- Bottom-right node-graph decoration -->
    <svg
      class="absolute bottom-0 right-0 h-72 w-72 text-emerald-500/10"
      viewBox="0 0 200 200"
      fill="none"
      stroke="currentColor"
      stroke-width="0.8"
    >
      <circle cx="40" cy="60" r="3" fill="currentColor" />
      <circle cx="120" cy="40" r="3" fill="currentColor" />
      <circle cx="160" cy="90" r="3" fill="currentColor" />
      <circle cx="100" cy="130" r="3" fill="currentColor" />
      <circle cx="50" cy="160" r="3" fill="currentColor" />
      <line x1="40" y1="60" x2="120" y2="40" />
      <line x1="120" y1="40" x2="160" y2="90" />
      <line x1="40" y1="60" x2="100" y2="130" />
      <line x1="100" y1="130" x2="160" y2="90" />
      <line x1="100" y1="130" x2="50" y2="160" />
    </svg>
  </div>
</template>

<script setup lang="ts">
// 8 deterministically-seeded particles. Positions/delays are fixed (not
// random per mount) so the layout is stable across hydration.
const particles = [
  { left: 12, top: 18, delay: 0, duration: 14 },
  { left: 28, top: 72, delay: 2, duration: 18 },
  { left: 45, top: 35, delay: 4, duration: 12 },
  { left: 62, top: 80, delay: 1, duration: 16 },
  { left: 78, top: 22, delay: 5, duration: 20 },
  { left: 88, top: 60, delay: 3, duration: 13 },
  { left: 8, top: 50, delay: 6, duration: 17 },
  { left: 55, top: 12, delay: 2.5, duration: 15 },
];
</script>

<style scoped>
.dashboard-bg-radial {
  background: radial-gradient(
    ellipse at 50% 30%,
    rgba(34, 197, 94, 0.06) 0%,
    rgba(6, 9, 15, 0) 60%
  );
}
.dashboard-bg-hex {
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='46' viewBox='0 0 40 46'><path d='M20 0L40 11.5v23L20 46 0 34.5v-23z' fill='none' stroke='%2334d399' stroke-opacity='0.05' stroke-width='0.5'/></svg>");
  background-repeat: repeat;
  opacity: 0.6;
}
.dashboard-particle {
  position: absolute;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #34d399;
  box-shadow: 0 0 8px #34d399;
  opacity: 0.4;
  animation: drift linear infinite;
}
@keyframes drift {
  0% { transform: translate(0, 0); opacity: 0; }
  20% { opacity: 0.5; }
  80% { opacity: 0.5; }
  100% { transform: translate(40px, -40px); opacity: 0; }
}
</style>
