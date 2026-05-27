<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
  <LoginModal v-if="loginModal.open" />
</template>

<script setup lang="ts">
const loginModal = useLoginModalStore();
</script>
